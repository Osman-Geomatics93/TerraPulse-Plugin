"""
Sentinel-1 SLC downloader for Copernicus Data Space Ecosystem (CDSE).

Runs INSIDE the Docker engine container — never imported in the QGIS plugin process.

Authentication: OAuth2 Resource Owner Password Credentials (ROPC) flow.
CDSE token endpoint:
  https://identity.dataspace.copernicus.eu/auth/realms/CDSE/protocol/openid-connect/token

Download URLs come from SentinelScene.assets["PRODUCT"] href strings.

Example usage (inside engine_server.py)::

    auth = CDSEAuth(username="user@email.com", password=os.environ["CDSE_PW"])
    token = auth.get_token()
    dl = CDSEDownloader()
    zip_path = dl.download_scene(
        url="https://download.dataspace.copernicus.eu/odata/v1/...",
        dest_dir=Path("/data/slc"),
        token=token,
        scene_id="S1A_IW_SLC_...",
        progress_cb=lambda done, total: print(f"{done}/{total}"),
    )
    safe_dir = dl.unzip_safe(zip_path, dest_dir=Path("/data/slc"))
"""

from __future__ import annotations

import concurrent.futures
import logging
import zipfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

import requests

logger = logging.getLogger(__name__)

# CDSE OAuth2 ROPC endpoint (it's a public URL, not a secret — Bandit B105
# false-positive because the string contains "token")
_TOKEN_URL = (  # nosec B105
    "https://identity.dataspace.copernicus.eu"
    "/auth/realms/CDSE/protocol/openid-connect/token"
)
_CLIENT_ID = "cdse-public"
_CHUNK_SIZE = 4 << 20   # 4 MiB per read chunk (larger = fewer syscalls)
_TOKEN_TIMEOUT_S = 30
_DOWNLOAD_TIMEOUT_S = 600  # 10 min per scene

# Parallel chunk download settings.
# CDSE throttles each TCP connection; opening N parallel Range connections
# multiplies effective throughput up to N×.
_N_PARALLEL_CONNECTIONS = 4          # simultaneous connections per file
_PARALLEL_MIN_SIZE_BYTES = 50 << 20  # only parallelize files > 50 MB

DownloadProgressCallback = Callable[[int, int], None]  # (bytes_done, bytes_total)


class CDSEAuthError(Exception):
    """Raised when CDSE OAuth2 token acquisition fails."""


class CDSEDownloadError(Exception):
    """Raised when a scene download fails."""


@dataclass
class CDSEAuth:
    """
    Manages CDSE OAuth2 token acquisition and refresh.

    Tokens expire after ~600 seconds. Call `get_token()` before each download;
    tokens are cached and refreshed automatically.
    """

    username: str
    password: str
    _cached_token: str | None = None

    def get_token(self) -> str:
        """
        Request a fresh OAuth2 access token from CDSE.

        Returns
        -------
        Bearer token string.

        Raises
        ------
        CDSEAuthError
            If CDSE returns a non-200 response or the token is missing.
        """
        logger.debug("Requesting CDSE OAuth2 token for %s", self.username)
        payload = {
            "grant_type": "password",
            "username": self.username,
            "password": self.password,
            "client_id": _CLIENT_ID,
        }
        try:
            resp = requests.post(
                _TOKEN_URL,
                data=payload,
                timeout=_TOKEN_TIMEOUT_S,
            )
        except requests.RequestException as exc:
            raise CDSEAuthError(f"Network error during CDSE auth: {exc}") from exc

        if resp.status_code != 200:
            raise CDSEAuthError(
                f"CDSE auth failed (HTTP {resp.status_code}): {resp.text[:200]}"
            )

        token = resp.json().get("access_token")
        if not token:
            raise CDSEAuthError("CDSE auth response missing 'access_token' field.")

        self._cached_token = token
        logger.debug("CDSE token acquired successfully.")
        return token


class CDSEDownloader:
    """
    Downloads Sentinel-1 SLC SAFE zip files from CDSE and extracts them.
    """

    def download_scene(
        self,
        url: str,
        dest_dir: Path,
        token: str,
        scene_id: str = "",
        progress_cb: DownloadProgressCallback | None = None,
    ) -> Path:
        """
        Download a single SLC SAFE zip from CDSE.

        Uses parallel HTTP Range connections when the server supports it, which
        multiplies effective throughput on connections throttled per-TCP by CDSE.

        Parameters
        ----------
        url:
            Direct download URL from ``SentinelScene.assets["product"]``.
        dest_dir:
            Local directory to save the zip file.
        token:
            Valid CDSE Bearer token from ``CDSEAuth.get_token()``.
        scene_id:
            Human-readable scene ID for logging.
        progress_cb:
            Called with ``(bytes_done, bytes_total)`` after each chunk.

        Returns
        -------
        Path to the downloaded zip file.

        Raises
        ------
        CDSEDownloadError
            On HTTP errors or incomplete downloads.
        """
        dest_dir.mkdir(parents=True, exist_ok=True)

        url_name = self._url_filename(url)
        zip_path = dest_dir / (url_name or f"{scene_id or 'scene'}.zip")

        if zip_path.exists() and self._is_valid_zip(zip_path):
            logger.info("Scene already cached and valid, skipping: %s", zip_path)
            return zip_path
        elif zip_path.exists():
            logger.warning("Cached file %s is not a valid zip — re-downloading.", zip_path)
            zip_path.unlink()

        auth_headers = {"Authorization": f"Bearer {token}"}

        # ------------------------------------------------------------------
        # Phase 1: Streaming GET — CDSE OData does NOT support HEAD (405).
        #   Open the connection, read headers, then either:
        #   (a) stream directly (single-threaded), or
        #   (b) capture the final S3 URL and re-open N parallel Range GETs.
        # ------------------------------------------------------------------
        total_bytes = 0
        final_url = url
        accepts_ranges = False
        resp_content_type = "unknown"
        single_thread_done = False

        try:
            with requests.get(
                url,
                headers=auth_headers,
                stream=True,
                timeout=_DOWNLOAD_TIMEOUT_S,
                allow_redirects=True,
            ) as resp:
                if resp.status_code == 401:
                    raise CDSEDownloadError(
                        "CDSE token expired or invalid (HTTP 401). "
                        "Check your CDSE username/password in Settings."
                    )
                if resp.status_code != 200:
                    preview = resp.text[:300]
                    raise CDSEDownloadError(
                        f"Download failed (HTTP {resp.status_code}): {url}\n"
                        f"Response: {preview}"
                    )

                # Capture metadata before reading body
                final_url = resp.url          # resolved S3 URL after redirect
                total_bytes = int(resp.headers.get("content-length", 0))
                accepts_ranges = (
                    resp.headers.get("accept-ranges", "").lower() == "bytes"
                )
                resp_content_type = resp.headers.get("content-type", "unknown")

                # Prefer filename from Content-Disposition
                cd = resp.headers.get("content-disposition", "")
                if "filename=" in cd:
                    cd_name = cd.split("filename=")[-1].strip().strip("\"'")
                    if cd_name and not cd_name.startswith("$"):
                        zip_path = dest_dir / cd_name

                use_parallel = (
                    accepts_ranges
                    and total_bytes >= _PARALLEL_MIN_SIZE_BYTES
                    and final_url != url   # successfully redirected to S3
                )

                logger.info(
                    "Downloading %s → %s  (%.2f GB, %s)",
                    scene_id or "scene",
                    zip_path.name,
                    total_bytes / 1e9,
                    f"{_N_PARALLEL_CONNECTIONS} parallel chunks"
                    if use_parallel else "single stream",
                )

                if not use_parallel:
                    # Use this open connection — avoids a second round-trip
                    bytes_done = 0
                    with open(zip_path, "wb") as f:
                        for chunk in resp.iter_content(chunk_size=_CHUNK_SIZE):
                            if chunk:
                                f.write(chunk)
                                bytes_done += len(chunk)
                                if progress_cb:
                                    progress_cb(bytes_done, total_bytes)
                    single_thread_done = True
                # else: connection closes here; parallel download uses final_url

        except CDSEDownloadError:
            raise
        except requests.RequestException as exc:
            if zip_path.exists():
                zip_path.unlink()
            raise CDSEDownloadError(f"Download interrupted: {exc}") from exc

        # ------------------------------------------------------------------
        # Phase 2 (parallel only): N Range GETs to the resolved S3 URL.
        #   S3 pre-signed URLs do not need an Authorization header.
        # ------------------------------------------------------------------
        if not single_thread_done:
            try:
                self._download_parallel_chunks(
                    final_url, zip_path, total_bytes,
                    _N_PARALLEL_CONNECTIONS, progress_cb,
                )
            except CDSEDownloadError:
                raise
            except Exception as exc:
                if zip_path.exists():
                    zip_path.unlink()
                raise CDSEDownloadError(f"Parallel download failed: {exc}") from exc

        # ------------------------------------------------------------------
        # Phase 3: Validate
        # ------------------------------------------------------------------
        actual_size = zip_path.stat().st_size
        if total_bytes > 0 and actual_size < total_bytes * 0.99:
            zip_path.unlink()
            raise CDSEDownloadError(
                f"Incomplete download: expected {total_bytes} bytes, "
                f"got {actual_size}."
            )

        if not self._is_valid_zip(zip_path):
            with open(zip_path, "rb") as f:
                preview = f.read(400).decode("utf-8", errors="replace")
            zip_path.unlink()
            raise CDSEDownloadError(
                f"CDSE did not return a ZIP file for {scene_id!r}.\n"
                f"Content-Type: {resp_content_type}\n"
                f"Server response preview: {preview[:300]!r}\n"
                "Possible causes:\n"
                "  • Invalid CDSE credentials — check Settings\n"
                "  • CDSE temporary outage — retry later\n"
                "  • Scene not available for download"
            )

        logger.info("Downloaded %.1f GB → %s", actual_size / 1e9, zip_path.name)
        return zip_path

    # ------------------------------------------------------------------
    # Internal download helpers
    # ------------------------------------------------------------------

    def _download_single(
        self,
        url: str,
        headers: dict[str, str],
        dest: Path,
        total_bytes: int,
        progress_cb: DownloadProgressCallback | None,
    ) -> None:
        """Single-threaded streaming GET to ``dest``."""
        try:
            with requests.get(
                url, headers=headers, stream=True,
                timeout=_DOWNLOAD_TIMEOUT_S, allow_redirects=True,
            ) as resp:
                resp.raise_for_status()
                bytes_done = 0
                with open(dest, "wb") as f:
                    for chunk in resp.iter_content(chunk_size=_CHUNK_SIZE):
                        if chunk:
                            f.write(chunk)
                            bytes_done += len(chunk)
                            if progress_cb:
                                progress_cb(bytes_done, total_bytes)
        except requests.RequestException as exc:
            if dest.exists():
                dest.unlink()
            raise CDSEDownloadError(f"Download interrupted: {exc}") from exc

    def _download_parallel_chunks(
        self,
        url: str,
        dest: Path,
        total_bytes: int,
        n_threads: int,
        progress_cb: DownloadProgressCallback | None,
    ) -> None:
        """
        Download ``url`` in ``n_threads`` parallel HTTP Range chunks, writing
        each chunk directly into its correct byte offset in ``dest``.

        Unlike the previous approach (temp ``.partN`` files + merge), this
        method pre-allocates ``dest`` once and each thread writes directly at
        its own offset, so peak disk usage equals the final file size — not 2×.

        Thread safety: each thread opens its own file descriptor, so concurrent
        ``seek + write`` operations never interleave.  Progress callbacks are
        invoked without holding any lock to prevent pipe-buffer deadlocks.
        """
        chunk_size = total_bytes // n_threads
        bytes_done_per = [0] * n_threads
        errors: list[Exception] = []

        # Pre-allocate the output file to its final size.
        # This fails fast (with a clear message) if there is insufficient disk
        # space, rather than crashing mid-download with an obscure OSError.
        try:
            with open(dest, "wb") as f:
                f.seek(total_bytes - 1)
                f.write(b"\x00")
        except OSError as exc:
            raise CDSEDownloadError(
                f"Cannot pre-allocate {total_bytes / 1e9:.1f} GB for download — "
                f"disk may be full: {exc}"
            ) from exc

        def _fetch(idx: int, start: int, end: int) -> None:
            range_hdr = {"Range": f"bytes={start}-{end}"}
            try:
                with requests.get(
                    url, headers=range_hdr, stream=True,
                    timeout=_DOWNLOAD_TIMEOUT_S,
                ) as r:
                    r.raise_for_status()
                    offset = start
                    # Each thread uses its own file handle to avoid seek conflicts
                    with open(dest, "r+b") as f:
                        f.seek(offset)
                        for data in r.iter_content(chunk_size=_CHUNK_SIZE):
                            if data:
                                f.write(data)
                                offset += len(data)
                                bytes_done_per[idx] += len(data)
                                if progress_cb:
                                    # No lock — minor over-count is fine for
                                    # progress display; holding a lock here would
                                    # cause all threads to block if the pipe is full
                                    progress_cb(sum(bytes_done_per), total_bytes)
            except Exception as exc:  # noqa: BLE001
                errors.append(exc)

        with concurrent.futures.ThreadPoolExecutor(max_workers=n_threads) as executor:
            for i in range(n_threads):
                start = i * chunk_size
                end = total_bytes - 1 if i == n_threads - 1 else start + chunk_size - 1
                executor.submit(_fetch, i, start, end)
            # ThreadPoolExecutor.__exit__ joins all futures before returning

        if errors:
            if dest.exists():
                dest.unlink()
            raise CDSEDownloadError(
                f"Parallel download failed ({len(errors)} chunk errors): {errors[0]}"
            )

        logger.debug(
            "Parallel download complete: %d chunks → %s", n_threads, dest.name
        )

    @staticmethod
    def _is_valid_zip(path: Path) -> bool:
        """Return True if ``path`` starts with the ZIP magic bytes (PK\\x03\\x04)."""
        try:
            with open(path, "rb") as f:
                return f.read(4) == b"PK\x03\x04"
        except OSError:
            return False

    def unzip_safe(self, zip_path: Path, dest_dir: Path) -> Path:
        """
        Extract a Sentinel-1 SAFE zip file.

        Parameters
        ----------
        zip_path:
            Path to the downloaded .zip file.
        dest_dir:
            Directory where the SAFE folder will be extracted.

        Returns
        -------
        Path to the extracted ``.SAFE`` directory.

        Raises
        ------
        CDSEDownloadError
            If the zip is corrupt or does not contain a .SAFE directory.
        """
        dest_dir.mkdir(parents=True, exist_ok=True)
        logger.info("Extracting %s → %s", zip_path.name, dest_dir)

        try:
            with zipfile.ZipFile(zip_path, "r") as zf:
                # Find the .SAFE top-level directory name
                safe_dirs = {
                    name.split("/")[0]
                    for name in zf.namelist()
                    if name.endswith(".SAFE") or ".SAFE/" in name
                }
                if not safe_dirs:
                    raise CDSEDownloadError(
                        f"No .SAFE directory found in {zip_path}. "
                        "The zip may be corrupt or not a Sentinel-1 SAFE archive."
                    )

                safe_name = safe_dirs.pop()
                safe_out = dest_dir / safe_name

                if safe_out.exists():
                    logger.info("SAFE directory already extracted: %s", safe_out)
                    return safe_out

                zf.extractall(dest_dir)
        except zipfile.BadZipFile as exc:
            raise CDSEDownloadError(f"Corrupt zip file {zip_path}: {exc}") from exc

        if not safe_out.exists():
            raise CDSEDownloadError(
                f"Expected SAFE directory not found after extraction: {safe_out}"
            )

        logger.info("Extracted: %s", safe_out)
        return safe_out

    def download_and_unzip(
        self,
        url: str,
        dest_dir: Path,
        token: str,
        scene_id: str = "",
        progress_cb: DownloadProgressCallback | None = None,
    ) -> Path:
        """
        Convenience: download + unzip in one call. Returns the .SAFE directory path.
        """
        zip_path = self.download_scene(
            url=url,
            dest_dir=dest_dir,
            token=token,
            scene_id=scene_id,
            progress_cb=progress_cb,
        )
        safe_dir = self.unzip_safe(zip_path, dest_dir)
        return safe_dir

    @staticmethod
    def _url_filename(url: str) -> str:
        """
        Extract a usable filename from a URL path component.

        Returns ``""`` for OData ``$value`` endpoints (caller falls back to
        ``{scene_id}.zip``).  The ``$`` prefix is the OData convention for
        stream-valued properties — it is not a meaningful filename.
        """
        parsed = urlparse(url)
        parts = parsed.path.rstrip("/").split("/")
        name = parts[-1] if parts else ""
        # $value (and any other $… OData keywords) are not filenames
        return "" if not name or name.startswith("$") else name
