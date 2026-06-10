import streamlit as st
import ee
import geemap.foliumap as geemap
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
# GEE INITIALIZATION (อัปเดตเวอร์ชันปลอดภัย)
# ==========================================
if 'ee_initialized' not in st.session_state:
    try:
        # ตรวจสอบความปลอดภัยในการดึงค่าจาก Secrets
        has_secret = False
        try:
            if "gcp_service_account" in st.secrets:
                has_secret = True
        except Exception:
            has_secret = False

        if has_secret:
            # ดึงข้อความ JSON ออกมา
            secret_json_str = st.secrets["gcp_service_account"]
            secret_dict = json.loads(secret_json_str)
            
            # ยืนยันสิทธิ์ผ่าน Service Account
            credentials = ee.ServiceAccountCredentials(secret_dict['client_email'], key_data=json.dumps(secret_dict))
            ee.Initialize(credentials, project=secret_dict.get('project_id', "gee1-498904"))
            st.session_state['ee_initialized'] = True
        else:
            # สำหรับการรันแบบ Local เครื่องส่วนตัว
            ee.Initialize(project="gee1-498904")
            st.session_state['ee_initialized'] = True
            
    except Exception as e:
        st.error(f"การเชื่อมต่อ GEE ล้มเหลว: {e}")
        st.info("คำแนะนำ: ตรวจสอบการตั้งค่ากุญแจลับ gcp_service_account ในหน้า App Secrets ของ Streamlit Cloud")
        
# ==========================================
# INTERACTIVE MAP SELECTION (CROP TOOL)
# ==========================================
st.subheader("📍 1. วาดพื้นที่บนแผนที่ (Select ROI)")

# สร้างแผนที่เริ่มต้น
m = folium.Map(location=[19.149, 100.154], zoom_start=12)
# เพิ่มเครื่องมือวาดรูป
draw_options = {
    'polyline': False,
    'circlemarker': False,
    'marker': False,
    'circle': False,
    'polygon': True,
    'rectangle': True,
}
draw = folium.plugins.Draw(export=False, draw_options=draw_options)
draw.add_to(m)

# แสดงแผนที่ใน Streamlit และรับค่ากลับ
output = st_folium(m, width=1100, height=500)

# ดึงข้อมูล Geometry จากสิ่งที่ผู้ใช้วาด
roi_geometry = None
if output and output.get('last_active_drawing'):
    geo_json = output['last_active_drawing']['geometry']
    roi_geometry = ee.Geometry(geo_json)
    st.success("✅ ตรวจพบพื้นที่ที่เลือกแล้ว!")
else:
    st.info("👆 กรุณาวาดรูปสี่เหลี่ยมหรือโปลิกอนลงบนแผนที่ด้านบน")

# ==========================================
# SIDEBAR SETTINGS
# ==========================================
st.sidebar.header("📅 ตั้งค่าข้อมูล")
start_date = st.sidebar.date_input("วันที่เริ่มต้น", datetime.date(2023, 1, 1))
end_date = st.sidebar.date_input("วันที่สิ้นสุด", datetime.date(2024, 1, 1))
cloud_cover = st.sidebar.slider("เมฆสูงสุด (%)", 5, 50, 10)
run_button = st.sidebar.button("🚀 เริ่มการประมวลผล", use_container_width=True)

# ==========================================
# ANALYSIS LOGIC
# ==========================================
if run_button and roi_geometry:
    start_date_str = start_date.strftime('%Y-%m-%d')
    end_date_str = end_date.strftime('%Y-%m-%d')

    with st.spinner("⏳ กำลังวิเคราะห์ข้อมูลจากดาวเทียม..."):
        try:
            # ROI จากการวาด
            roi = roi_geometry

            # ดึงข้อมูล DEM และ Sentinel-2
            dem = ee.Image('USGS/SRTMGL1_003').clip(roi)
            s2_col = ee.ImageCollection('COPERNICUS/S2_SR_HARMONIZED') \
                .filterBounds(roi) \
                .filterDate(start_date_str, end_date_str) \
                .filter(ee.Filter.lt('CLOUDY_PIXEL_PERCENTAGE', cloud_cover))
            
            s2 = s2_col.median().clip(roi)

            # คำนวณดัชนี
            ndvi = s2.normalizedDifference(['B8', 'B4']).rename('NDVI')
            ndmi = s2.normalizedDifference(['B8', 'B11']).rename('NDMI')
            bsi = s2.expression('((B11 + B4) - (B8 + B2)) / ((B11 + B4) + (B8 + B2))',
                                {'B11': s2.select('B11'), 'B4': s2.select('B4'),
                                 'B8': s2.select('B8'), 'B2': s2.select('B2')}).rename('BSI')
            slope = ee.Terrain.slope(dem)
            elev = dem.select('elevation')

            # วิเคราะห์เขาหัวโล้น
            mountain = elev.gt(400).And(slope.gt(5)).rename('Mountain')
            deforested = ndvi.lt(0.5).And(mountain)
            intact_forest = ndvi.gt(0.65)
            distance_to_forest = intact_forest.distance(ee.Kernel.euclidean(2000, 'meters'))

            # Models
            ready_high = bsi.lt(0.2).And(ndmi.gt(-0.1)).And(distance_to_forest.lt(500)).And(deforested)
            ready_low = bsi.gt(0.3).Or(ndmi.lt(-0.2)).Or(distance_to_forest.gte(1000)).And(deforested)
            ready_med = deforested.And(ready_high.Not()).And(ready_low.Not())

            suitability_map = ee.Image(0).clip(roi)
            suitability_map = suitability_map.where(ready_low, 1).where(ready_med, 2).where(ready_high, 3)

            # Scoring
            score_ndvi = ee.Image(0.5).subtract(ndvi).divide(0.4).multiply(100).clamp(0, 100)
            score_slope = ee.Image(35).subtract(slope).divide(35).multiply(100).clamp(0, 100)
            score_dist = ee.Image(500).subtract(distance_to_forest).divide(500).multiply(100).clamp(0, 100)
            final_score = score_ndvi.multiply(0.4).add(score_slope.multiply(0.3)).add(score_dist.multiply(0.3)).round()
            score_map = final_score.updateMask(deforested).unmask(-1)

            # Export data to array
            export_img = s2.select(['B4', 'B3', 'B2']).addBands([ndvi, suitability_map.rename('Suitability'), score_map.rename('Score'), elev, mountain])
            
            # ใช้พิกัด ROI เพื่อดึง Numpy Array
            img_array = geemap.ee_to_numpy(export_img, region=roi, scale=20)

            if img_array is not None:
                # Visualization Layers
                rgb_data = np.clip(img_array[:, :, 0:3].astype(float) / 2500.0, 0, 1)
                ndvi_data = img_array[:, :, 3].astype(float)
                suit_data = img_array[:, :, 4].astype(float)
                suit_data[suit_data == 0] = np.nan
                score_data = img_array[:, :, 5].astype(float)
                score_data[score_data == -1] = np.nan
                elev_data = img_array[:, :, 6].astype(float)
                mountain_data = img_array[:, :, 7].astype(float)
                mountain_mask = np.where(mountain_data == 1, 1.0, np.nan)

                # Tabs สำหรับรายงาน
                st.subheader("📊 2. ผลการวิเคราะห์พื้นที่")
                t1, t2, t3 = st.tabs(["🖼️ NDVI & Terrain", "🎯 Suitability", "📈 Priority Score"])

                with t1:
                    col1, col2 = st.columns(2)
                    with col1:
                        st.caption("True Color (Sentinel-2)")
                        fig_rgb, ax_rgb = plt.subplots()
                        ax_rgb.imshow(rgb_data)
                        ax_rgb.axis('off')
                        st.pyplot(fig_rgb)
                    with col2:
                        st.caption("NDVI (ความเขียวของพืชพรรณ)")
                        fig_ndvi, ax_ndvi = plt.subplots()
                        im = ax_ndvi.imshow(ndvi_data, cmap='RdYlBu', vmin=0, vmax=0.8)
                        plt.colorbar(im, ax=ax_ndvi)
                        ax_ndvi.axis('off')
                        st.pyplot(fig_ndvi)

                with t2:
                    st.caption("Forest Restoration Suitability")
                    fig_suit, ax_suit = plt.subplots()
                    ax_suit.imshow(rgb_data)
                    cmap_s = mcolors.ListedColormap(['#d62728', '#ffea00', '#00ffeb'])
                    ax_suit.imshow(suit_data, cmap=cmap_s, alpha=0.7)
                    ax_suit.axis('off')
                    st.pyplot(fig_suit)
                    st.info("🔴 แดง: วิกฤต | 🟡 เหลือง: ปานกลาง | 🔵 ฟ้า: พร้อมฟื้นฟูสูง")

                with t3:
                    st.caption("Restoration Priority Score (0-100)")
                    fig_sc, ax_sc = plt.subplots()
                    ax_sc.imshow(rgb_data)
                    im_sc = ax_sc.imshow(score_data, cmap='RdYlGn', vmin=0, vmax=100, alpha=0.7)
                    plt.colorbar(im_sc, ax=ax_sc)
                    ax_sc.axis('off')
                    st.pyplot(fig_sc)

            st.success("✅ วิเคราะห์เสร็จสิ้น!")

        except Exception as e:
            st.error(f"เกิดข้อผิดพลาด: {e}")

elif run_button and not roi_geometry:
    st.warning("⚠️ กรุณาวาดพื้นที่บนแผนที่ก่อนกดปุ่มประมวลผล")
