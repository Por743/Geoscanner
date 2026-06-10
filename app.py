import streamlit as st
import ee
import geemap
import numpy as np
import matplotlib.pyplot as plt
import matplotlib.colors as mcolors
import matplotlib.patches as mpatches
import datetime
import json
import folium
from streamlit_folium import st_folium

# ตั้งค่าหน้าเว็บ Streamlit
st.set_page_config(page_title="Forest Restoration Map Tool", layout="wide")

# ==========================================
# GEE INITIALIZATION
# ==========================================
if 'ee_initialized' not in st.session_state:
    try:
        if "gcp_service_account" in st.secrets:
            secret_dict = json.loads(st.secrets["gcp_service_account"])
            credentials = ee.ServiceAccountCredentials(secret_dict['client_email'], key_data=json.dumps(secret_dict))
            ee.Initialize(credentials, project=secret_dict.get('project_id', "gee1-498904"))
        else:
            ee.Initialize(project="gee1-498904")
        st.session_state['ee_initialized'] = True
    except Exception as e:
        st.error(f"การเชื่อมต่อ GEE ล้มเหลว: {e}")

st.title("🌱 ECORESTORE AI",frontsize=20)
st.write("เครื่องมือประมวลผลดัชนีพืชพรรณและภูมิประเทศผ่านการระบุพิกัด BBox หรืออัปโหลดไฟล์ GeoJSON")

# ==========================================
# SIDEBAR CONTROLS (ระบบเลือกเเละนำเข้าพื้นที่ AOI)
# ==========================================
st.sidebar.header("🎯 1. นำเข้าขอบเขตพื้นที่ศึกษา (AOI)")
input_mode = st.sidebar.radio("เลือกรูปแบบการกำหนดพื้นที่:", ["กรอกพิกัด BBox Array", "อัปโหลดไฟล์ GeoJSON"])

# สร้างตัวแปรส่วนกลางสำหรับเก็บข้อมูล Geometry ใน Session
if 'current_roi_dict' not in st.session_state:
    st.session_state['current_roi_dict'] = None

if input_mode == "กรอกพิกัด BBox Array":
    st.sidebar.write("รูปแบบพิกัด: `[Min Lon, Min Lat, Max Lon, Max Lat]`")
    
    col1, col2 = st.sidebar.columns(2)
    with col1:
        min_lon = st.number_input("West (Min Lon)", value=100.1000, format="%.4f")
        min_lat = st.number_input("South (Min Lat)", value=19.1000, format="%.4f")
    with col2:
        max_lon = st.number_input("East (Max Lon)", value=100.2000, format="%.4f")
        max_lat = st.number_input("North (Max Lat)", value=19.2000, format="%.4f")
        
    if st.sidebar.button("📍 ยืนยันพิกัด BBox"):
        if min_lon < max_lon and min_lat < max_lat:
            bbox_ee = ee.Geometry.Rectangle([min_lon, min_lat, max_lon, max_lat])
            st.session_state['current_roi_dict'] = bbox_ee.getInfo()
            st.sidebar.success("บันทึกขอบเขตพิกัด BBox เรียบร้อย!")
        else:
            st.sidebar.error("พิกัดไม่ถูกต้อง (ค่า Min ต้องน้อยกว่าค่า Max)")

else:
    uploaded_file = st.sidebar.file_uploader("เลือกไฟล์คีย์ขอบเขต (.geojson)", type=["geojson", "json"])
    if uploaded_file is not None:
        try:
            geojson_data = json.load(uploaded_file)
            
            if geojson_data['type'] == 'FeatureCollection':
                if len(geojson_data['features']) > 0:
                    geom = geojson_data['features'][0]['geometry']
                else:
                    raise ValueError("ไม่พบข้อมูล Feature ในคอลเลกชัน")
            elif geojson_data['type'] == 'Feature':
                geom = geojson_data['geometry']
            else:
                geom = geojson_data
                
            st.session_state['current_roi_dict'] = geom
            st.sidebar.success("อ่านและบันทึกโครงสร้าง GeoJSON สำเร็จ!")
        except Exception as e:
            st.sidebar.error(f"การอ่านไฟล์ล้มเหลว: {e}")

st.sidebar.header("📅 2. ช่วงเวลากรองภาพถ่าย")
start_date = st.sidebar.date_input("วันที่เริ่มต้น", datetime.date(2023, 1, 1))
end_date = st.sidebar.date_input("วันที่สิ้นสุด", datetime.date(2024, 1, 1))
cloud_cover = st.sidebar.slider("เกณฑ์เมฆสูงสุด (%)", 5, 50, 10)
run_button = st.sidebar.button("🚀 เริ่มการประมวลผลพื้นที่", use_container_width=True)

# ==========================================
# PREVIEW MAP
# ==========================================
st.subheader("📍 ตรวจสอบขอบเขตพื้นที่นำเข้า")
if st.session_state['current_roi_dict'] is not None:
    try:
        roi_geo = ee.Geometry(st.session_state['current_roi_dict'])
        center = roi_geo.centroid().coordinates().getInfo()
        
        preview_map = folium.Map(location=[center[1], center[0]], zoom_start=12)
        folium.GeoJson(
            st.session_state['current_roi_dict'],
            name="Selected AOI",
            style_function=lambda x: {'fillColor': '#00ffeb', 'color': '#00ffeb', 'weight': 2, 'fillOpacity': 0.2}
        ).add_to(preview_map)
        
        st_folium(preview_map, width=1100, height=400, key="preview_map")
    except Exception as e:
        st.caption("ระบบกำลังโหลดแผนที่ขอบเขต...")
else:
    st.info("👆 กรุณากำหนดพิกัด BBox หรืออัปโหลดไฟล์ GeoJSON ที่เมนูด้านซ้ายเพื่อเริ่มต้นเปิดแผนที่แสดงขอบเขต")

# ==========================================
# ANALYSIS LOGIC
# ==========================================
if run_button and st.session_state['current_roi_dict'] is not None:
    start_date_str = start_date.strftime('%Y-%m-%d')
    end_date_str = end_date.strftime('%Y-%m-%d')

    with st.spinner("⏳ กำลังดึงภาพถ่ายดาวเทียมและคำนวณแบบจำลองเชิงพื้นที่..."):
        try:
            roi = ee.Geometry(st.session_state['current_roi_dict'])

            dem = ee.Image('USGS/SRTMGL1_003').clip(roi)
            s2_col = ee.ImageCollection('COPERNICUS/S2_SR_HARMONIZED') \
                .filterBounds(roi) \
                .filterDate(start_date_str, end_date_str) \
                .filter(ee.Filter.lt('CLOUDY_PIXEL_PERCENTAGE', cloud_cover))
            
            s2 = s2_col.median().clip(roi)

            if s2.bandNames().size().getInfo() == 0:
                st.error("❌ ไม่พบภาพดาวเทียมที่สอดคล้องตามเงื่อนไขเวลาและเปอร์เซ็นต์เมฆในพื้นที่นี้")
            else:
                ndvi = s2.normalizedDifference(['B8', 'B4']).rename('NDVI')
                ndmi = s2.normalizedDifference(['B8', 'B11']).rename('NDMI')
                bsi = s2.expression('((B11 + B4) - (B8 + B2)) / ((B11 + B4) + (B8 + B2))',
                                    {'B11': s2.select('B11'), 'B4': s2.select('B4'),
                                     'B8': s2.select('B8'), 'B2': s2.select('B2')}).rename('BSI')
                slope = ee.Terrain.slope(dem)
                elev = dem.select('elevation')

                mountain = elev.gt(400).And(slope.gt(5)).rename('Mountain')
                deforested = ndvi.lt(0.5).And(mountain)
                intact_forest = ndvi.gt(0.65)
                distance_to_forest = intact_forest.distance(ee.Kernel.euclidean(2000, 'meters'))

                ready_high = bsi.lt(0.2).And(ndmi.gt(-0.1)).And(distance_to_forest.lt(500)).And(deforested)
                ready_low = bsi.gt(0.3).Or(ndmi.lt(-0.2)).Or(distance_to_forest.gte(1000)).And(deforested)
                ready_med = deforested.And(ready_high.Not()).And(ready_low.Not())

                suitability_map = ee.Image(0).clip(roi)
                suitability_map = suitability_map.where(ready_low, 1).where(ready_med, 2).where(ready_high, 3)

                score_ndvi = ee.Image(0.5).subtract(ndvi).divide(0.4).multiply(100).clamp(0, 100)
                score_slope = ee.Image(35).subtract(slope).divide(35).multiply(100).clamp(0, 100)
                score_dist = ee.Image(500).subtract(distance_to_forest).divide(500).multiply(100).clamp(0, 100)
                final_score = score_ndvi.multiply(0.4).add(score_slope.multiply(0.3)).add(score_dist.multiply(0.3)).round()
                score_map = final_score.updateMask(deforested).unmask(-1)

                export_img = s2.select(['B4', 'B3', 'B2']).addBands([ndvi, suitability_map.rename('Suitability'), score_map.rename('Score'), elev, mountain])
                img_array = geemap.ee_to_numpy(export_img, region=roi, scale=20)

                if img_array is not None:
                    rgb_data = np.clip(img_array[:, :, 0:3].astype(float) / 2500.0, 0, 1)
                    ndvi_data = img_array[:, :, 3].astype(float)
                    suit_data = img_array[:, :, 4].astype(float)
                    suit_data[suit_data == 0] = np.nan
                    
                    score_data = img_array[:, :, 5].astype(float)
                    score_data[score_data == -1] = np.nan
                    
                    # ดึงข้อมูลระดับความสูงเชิงเลขและ Mask พื้นที่ภูเขามาเก็บในตัวแปรพล็อต
                    elev_data = img_array[:, :, 6].astype(float)
                    mountain_data = img_array[:, :, 7].astype(float)
                    mountain_mask = np.where(mountain_data == 1, 1.0, np.nan)

                    st.subheader("📊 ผลลัพธ์การประมวลผลเชิงพื้นที่ รายงานแยกตามเลเยอร์")
                    t1, t2, t3 = st.tabs(["🖼️ NDVI & Terrain", "🎯 ความเหมาะสมในการฟื้นฟู", "📈 คะแนนลำดับความสำคัญ"])

                    # ---------------------------------------------------------
                    # แก้ไข: เพิ่มข้อมูล Elevation เข้ามาแสดงในแท็บที่ 1 (ใช้ระบบ 3 คอลัมน์)
                    # ---------------------------------------------------------
                    with t1:
                        col1, col2, col3 = st.columns(3)  # ปรับเพิ่มจาก 2 เป็น 3 คอลัมน์
                        with col1:
                            st.caption("ภาพถ่ายสีจริง True Color (Sentinel-2 Composite)")
                            fig_rgb, ax_rgb = plt.subplots()
                            ax_rgb.imshow(rgb_data)
                            ax_rgb.axis('off')
                            st.pyplot(fig_rgb)
                        with col2:
                            st.caption("ดัชนีความเขียวพืชพรรณ (NDVI Verification)")
                            fig_ndvi, ax_ndvi = plt.subplots()
                            im = ax_ndvi.imshow(ndvi_data, cmap='RdYlBu', vmin=0, vmax=0.8)
                            plt.colorbar(im, ax=ax_ndvi, orientation='horizontal', pad=0.05)
                            ax_ndvi.axis('off')
                            st.pyplot(fig_ndvi)
                        with col3:
                            st.caption("แบบจำลองความสูงเชิงเลข (SRTM DEM Elevation)")
                            fig_elev, ax_elev = plt.subplots()
                            # พล็อตแผนที่ระดับความสูง (DEM) ด้วยคัลเลอร์แมปภูมิประเทศ 'terrain'
                            im_elev = ax_elev.imshow(elev_data, cmap='terrain')
                            
                            # เสริมเลเยอร์ไฮไลท์สีม่วงโปร่งแสงสำหรับเขตที่ระบบจำแนกเป็น "พื้นที่ภูเขา" (Mountain Area)
                            ax_elev.imshow(mountain_mask, cmap='Purples', alpha=0.4, vmin=0, vmax=1.5)
                            
                            plt.colorbar(im_elev, ax=ax_elev, orientation='horizontal', pad=0.05, label='Meters ASL')
                            ax_elev.axis('off')
                            
                            # เพิ่มสัญลักษณ์อธิบายแผนที่ (Legend) ของพิกัดภูเขา
                            mountain_patch = mpatches.Patch(color='#8c96c6', label='Mountain Mask\n(Elev > 400m & Slope > 5°)')
                            ax_elev.legend(handles=[mountain_patch], loc='lower right', fontsize=8)
                            st.pyplot(fig_elev)

                    with t2:
                        st.caption("แผนที่จำแนกความเหมาะสมในการฟื้นฟูสภาพป่าไม้ (Restoration Suitability Map)")
                        fig_suit, ax_suit = plt.subplots()
                        ax_suit.imshow(rgb_data)
                        cmap_s = mcolors.ListedColormap(['#d62728', '#ffea00', '#00ffeb'])
                        ax_suit.imshow(suit_data, cmap=cmap_s, alpha=0.7)
                        ax_suit.axis('off')
                        st.pyplot(fig_suit)
                        st.info("คำอธิบายกลุ่มพื้นที่ 🔴 สีแดง: พื้นที่วิกฤตสูง | 🟡 สีเหลือง: ความเร่งด่วนปานกลาง | 🔵 สีฟ้า: พื้นที่พร้อมฟื้นฟูสูง")

                    with t3:
                        st.caption("แผนที่ให้คะแนนพื้นที่เป้าหมายระดับย่อย (Restoration Priority Score: 0-100)")
                        fig_sc, ax_sc = plt.subplots()
                        ax_sc.imshow(rgb_data)
                        im_sc = ax_sc.imshow(score_data, cmap='RdYlGn', vmin=0, vmax=100, alpha=0.7)
                        plt.colorbar(im_sc, ax=ax_sc)
                        ax_sc.axis('off')
                        st.pyplot(fig_sc)

                    st.success("✅ การประมวลผลและการจัดทำข้อมูลสรุปภาพแผนที่เสร็จสิ้นสมบูรณ์!")
                else:
                    st.error("❌ ขอบเขตพื้นที่ศึกษาที่กำหนดมีขนาดใหญ่เกินไปหรือไม่มีชุดข้อมูลพิกเซลพิกัดตอบรับกลับมา")
        except Exception as e:
            st.error(f"เกิดข้อผิดพลาดระหว่างประมวลผล: {e}")

elif run_button and st.session_state['current_roi_dict'] is None:
    st.warning("⚠️ กรุณากดปุ่มยืนยันพิกัด BBox หรือ อัปโหลดไฟล์ GeoJSON ให้ระบบบันทึกค่าเรียบร้อยก่อนทำการกดปุ่มประมวลผล")
