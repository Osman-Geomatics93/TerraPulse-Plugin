<!DOCTYPE qgis PUBLIC 'http://mrcc.com/qgis.dtd' 'SYSTEM'>
<qgis version="3.34" styleCategories="AllStyleCategories">
  <!--
    TerraPulse LOS Velocity Raster Style
    Colour ramp: blue (uplift) → white (stable) → red (subsidence)
    Units: mm/year. Range: -50 to +50 mm/yr
    Apply with: layer.loadNamedStyle('/path/to/velocity.qml')
  -->
  <pipe>
    <provider>
      <resampling enabled="false" maxOversampling="2" zoomedOutResamplingMethod="nearestNeighbour" zoomedInResamplingMethod="nearestNeighbour"/>
    </provider>
    <rasterrenderer type="singlebandpseudocolor" classificationMax="50" classificationMin="-50" band="1" opacity="0.85">
      <rasterTransparency/>
      <minMaxOrigin>
        <limits>MinMax</limits>
        <extent>WholeRaster</extent>
        <statAccuracy>Estimated</statAccuracy>
        <cumulativeCutLower>0.02</cumulativeCutLower>
        <cumulativeCutUpper>0.98</cumulativeCutUpper>
        <stdDevFactor>2</stdDevFactor>
      </minMaxOrigin>
      <rastershader>
        <colorrampshader clip="0" colorRampType="INTERPOLATED" classificationMode="1" minimumValue="-50" maximumValue="50" labelPrecision="1">
          <colorramp name="[source]" type="gradient">
            <Option type="Map">
              <Option name="color1" value="5,48,97,255" type="QString"/>
              <Option name="color2" value="103,0,31,255" type="QString"/>
              <Option name="direction" value="ccw" type="QString"/>
              <Option name="discrete" value="0" type="QString"/>
              <Option name="rampType" value="gradient" type="QString"/>
            </Option>
          </colorramp>
          <!-- Subsidence (negative = moving toward satellite in descending, away in ascending) -->
          <item value="-50"  label="≤ -50 mm/yr" color="#053061" alpha="255"/>
          <item value="-20"  label="-20 mm/yr"   color="#2166AC" alpha="255"/>
          <item value="-10"  label="-10 mm/yr"   color="#4393C3" alpha="255"/>
          <item value="-5"   label="-5 mm/yr"    color="#92C5DE" alpha="255"/>
          <item value="-2"   label="-2 mm/yr"    color="#D1E5F0" alpha="255"/>
          <!-- Stable -->
          <item value="0"    label="Stable"       color="#F7F7F7" alpha="255"/>
          <!-- Uplift -->
          <item value="2"    label="+2 mm/yr"    color="#FDDBC7" alpha="255"/>
          <item value="5"    label="+5 mm/yr"    color="#F4A582" alpha="255"/>
          <item value="10"   label="+10 mm/yr"   color="#D6604D" alpha="255"/>
          <item value="20"   label="+20 mm/yr"   color="#B2182B" alpha="255"/>
          <item value="50"   label="≥ +50 mm/yr" color="#67001F" alpha="255"/>
        </colorrampshader>
      </rastershader>
    </rasterrenderer>
    <brightnesscontrast gamma="1" brightness="0" contrast="0"/>
    <huesaturation colorizeRed="255" colorizeOn="0" colorizeGreen="128" grayscaleMode="0" saturation="0" colorizeBlue="128" colorizeStrength="100"/>
    <rasterresampler maxOversampling="2"/>
    <resamplingStage>resamplingFilter</resamplingStage>
  </pipe>
  <blendMode>0</blendMode>
</qgis>
