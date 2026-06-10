import streamlit as st
import ee
import geemap
import numpy as np
import matplotlib.pyplot as plt
import matplotlib.colors as mcolors
import matplotlib.patches as mpatches
import datetime
import json

# ตั้งค่าหน้าเว็บ Streamlit
st.set_page_config(page_title="Forest Restoration & Terrain Analytics", layout="wide")

# ==========================================
# GEE INITIALIZATION & AUTHENTICATION
# ==========================================
# ระบบรองรับการใช้งานทั้งการรันโลคอล และการดีพลอยขึ้น Streamlit Cloud ด้วย Service Account
if 'ee_initialized' not in st.session_state:
    try:
        # ตรวจสอบว่ามี Credentials ใน Streamlit Secrets หรือไม่ (สำหรับคลาวด์)
        if "gcp_service_account" in st.secrets:
            secret_dict = json.loads(st.secrets["gcp_service_account"])
            credentials = ee.ServiceAccountCredentials(secret_dict['client_email'], key_data=json.dumps(secret_dict))
            ee.Initialize(credentials, project=secret_dict.get('project_id', "gee1-498904"))
        else:
            # รันในเครื่องส่วนตัว (Local) ระบบจะดึงสิทธิ์จาก gcloud/ee.Authenticate() อัตโนมัติ
            ee.Initialize(project="gee1-498904")
        st.session_state['ee_initialized'] = True
    except Exception as e:
        st.error(f"ไม่สามารถเชื่อมต่อ Google Earth Engine ได้: {e}")
        st.info("คำแนะนำ: หากใช้งานบน Streamlit Cloud ต้องตั้งค่ากุญแจลับ gcp_service_account ในหน้า App Secrets")

st.title("🌱 ระบบวิเคราะห์พื้นที่เขาหัวโล้นและการประเมินฟื้นฟูป่าไม้")
st.write("เครื่องมือประมวลผลภาพดาวเทียม Sentinel-2 และข้อมูลระดับความสูงเชิงเลข (SRTM DEM) เชิงพื้นที่แบบทันที (On-the-fly Analytics)")

# ==========================================
# SIDEBAR CONTROLS (ระบบรับ Input ภาพพิกัดใหม่)
# ==========================================
st.sidebar.header("🎯 เลือกพิกัดพื้นที่ศึกษาใหม่")

# เลือกพิกัดเริ่มต้น (Default: อ.บ่อเกลือ จ.น่าน)
lon_input = st.sidebar.number_input("ลองจิจูด (Longitude)", value=100.154, format="%.4f")
lat_input = st.sidebar.number_input("ลติจูด (Latitude)", value=19.149, format="%.4f")
buffer_input = st.sidebar.slider("ระยะรัศมีตรวจสอบ (เมตร)", min_value=1000, max_value=5000, value=2500, step=500)

st.sidebar.header("📅 ช่วงเวลากรองภาพดาวเทียม")
start_date = st.sidebar.date_input("วันที่เริ่มต้น", datetime.date(2023, 1, 1))
end_date = st.sidebar.date_input("วันที่สิ้นสุด", datetime.date(2024, 1, 1))
cloud_cover = st.sidebar.slider("เกณฑ์เปอร์เซ็นต์เมฆสูงสุด (%)", min_value=5, max_value=50, value=10, step=5)

run_button = st.sidebar.button("🚀 ประมวลผลและสร้างสไลด์รายงาน", use_container_width=True)

# แปลงวันเวลาให้อยู่ในฟอร์แมตข้อความเพื่อใช้ใน GEE
start_date_str = start_date.strftime('%Y-%m-%d')
end_date_str = end_date.strftime('%Y-%m-%d')

# รันโมเดลคำนวณเมื่อกดปุ่ม
if run_button and st.session_state.get('ee_initialized', False):
    
    with st.spinner("⏳ กำลังดาวน์โหลดข้อมูลดาวเทียมและคำนวณดัชนีวิเคราะห์ทางภูมิศาสตร์..."):
        try:
            # สร้างพื้นที่ขอบเขตความสนใจ (ROI) ตามตำแหน่งอินพุตใหม่
            roi = ee.Geometry.Point([lon_input, lat_input]).buffer(buffer_input).bounds()

            # ---------------------------------------------------------
            # DATA INGESTION & INDICES CALCULATION
            # ---------------------------------------------------------
            dem = ee.Image('USGS/SRTMGL1_003').clip(roi)
            s2 = ee.ImageCollection('COPERNICUS/S2_SR_HARMONIZED') \
                .filterBounds(roi) \
                .filterDate(start_date_str, end_date_str) \
                .filter(ee.Filter.lt('CLOUDY_PIXEL_PERCENTAGE', cloud_cover)) \
                .median() \
                .clip(roi)

            # ตรวจสอบแบนด์ข้อมูล
            if s2.bandNames().size().getInfo() == 0:
                st.error("❌ ไม่พบภาพถ่ายดาวเทียมที่ผ่านเกณฑ์ในช่วงเวลาดังกล่าว กรุณาขยายช่วงเวลาหรือเพิ่มเกณฑ์เปอร์เซ็นต์เมฆ")
            else:
                ndvi = s2.normalizedDifference(['B8', 'B4']).rename('NDVI')
                ndmi = s2.normalizedDifference(['B8', 'B11']).rename('NDMI')
                bsi = s2.expression('((B11 + B4) - (B8 + B2)) / ((B11 + B4) + (B8 + B2))',
                                    {'B11': s2.select('B11'), 'B4': s2.select('B4'),
                                     'B8': s2.select('B8'), 'B2': s2.select('B2')}).rename('BSI')
                slope = ee.Terrain.slope(dem)
                elev = dem.select('elevation')

                # คัดกรองเขตป่าเสื่อมโทรมบริเวณภูเขา
                mountain = elev.gt(400).And(slope.gt(5)).rename('Mountain')
                deforested = ndvi.lt(0.5).And(mountain)
                intact_forest = ndvi.gt(0.65)
                distance_to_forest = intact_forest.distance(ee.Kernel.euclidean(2000, 'meters'))

                # ---------------------------------------------------------
                # ANALYTICAL MODELS
                # ---------------------------------------------------------
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

                # รวบรวมแบนด์ส่งออก
                export_img = s2.select(['B4', 'B3', 'B2']) \
                    .addBands(ndvi) \
                    .addBands(suitability_map.rename('Suitability')) \
                    .addBands(score_map.rename('Score')) \
                    .addBands(elev) \
                    .addBands(mountain)

                # แปลงข้อมูลภาพจาก Earth Engine เป็น Numpy Array สำหรับ Matplotlib
                img_array = geemap.ee_to_numpy(export_img, region=roi, scale=15)

                if img_array is not None:
                    # เตรียมข้อมูลเลเยอร์พล็อตกราฟ
                    rgb_data = np.clip(img_array[:, :, 0:3].astype(float) / 2500.0, 0, 1)
                    ndvi_data = img_array[:, :, 3].astype(float)
                    suit_data = img_array[:, :, 4].astype(float)
                    suit_data[suit_data == 0] = np.nan

                    score_data = img_array[:, :, 5].astype(float)
                    score_data[score_data == -1] = np.nan
                    
                    elev_data = img_array[:, :, 6].astype(float)
                    mountain_data = img_array[:, :, 7].astype(float)
                    mountain_mask = np.where(mountain_data == 1, 1.0, np.nan)

                    # ---------------------------------------------------------
                    # STREAMLIT TABS UI (แบ่งกลุ่มสไลด์รายงานให้สลับดูง่าย)
                    # ---------------------------------------------------------
                    tab1, tab2, tab3, tab4 = st.tabs([
                        "📸 Slide 1: Context & NDVI", 
                        "⛰️ Slide 1.5: Terrain Analysis", 
                        "🎯 Slide 2: Suitability Zones", 
                        "📊 Slide 3: Priority Scoring"
                    ])

                    with tab1:
                        st.subheader("Phase 1: Target Area Identification")
                        fig1, axes1 = plt.subplots(1, 2, figsize=(16, 7))
                        axes1[0].imshow(rgb_data, interpolation='bilinear')
                        axes1[0].set_title('A. True Color Composite (Sentinel-2)', fontsize=14, weight='bold')
                        axes1[0].axis('off')

                        im_ndvi = axes1[1].imshow(ndvi_data, cmap='RdYlBu', vmin=0.1, vmax=0.8)
                        axes1[1].set_title('B. Vegetation Index (NDVI) Verification', fontsize=14, weight='bold')
                        axes1[1].axis('off')
                        cbar1 = fig1.colorbar(im_ndvi, ax=axes1[1], fraction=0.046, pad=0.04)
                        cbar1.set_label('NDVI Value (Red = Degraded, Blue = Intact)', fontsize=10)
                        plt.tight_layout()
                        st.pyplot(fig1)

                    with tab2:
                        st.subheader("Phase 1.5: Topographic & Terrain Analysis")
                        fig1_5, axes1_5 = plt.subplots(1, 2, figsize=(16, 7))
                        im_elev = axes1_5[0].imshow(elev_data, cmap='terrain')
                        axes1_5[0].set_title('A. Digital Elevation Model (DEM)', fontsize=14, weight='bold')
                        axes1_5[0].axis('off')
                        cbar_elev = fig1_5.colorbar(im_elev, ax=axes1_5[0], fraction=0.046, pad=0.04)
                        cbar_elev.set_label('Elevation (Meters above sea level)', fontsize=10)

                        axes1_5[1].imshow(rgb_data, interpolation='bilinear')
                        im_mount = axes1_5[1].imshow(mountain_mask, cmap='Purples', alpha=0.5, vmin=0, vmax=1.5)
                        axes1_5[1].set_title('B. Mountain Masking Area', fontsize=14, weight='bold')
                        axes1_5[1].axis('off')
                        
                        mountain_patch = mpatches.Patch(color='#8c96c6', label='Mountain Area\n(Elevation > 400m & Slope > 5°)')
                        axes1_5[1].legend(handles=[mountain_patch], loc='lower right', fontsize=10)
                        plt.tight_layout()
                        st.pyplot(fig1_5)

                    with tab3:
                        st.subheader("Phase 2: Forest Restoration Suitability Zones")
                        fig2, ax2 = plt.subplots(figsize=(10, 6))
                        ax2.imshow(rgb_data, interpolation='bilinear')

                        cmap_suit = mcolors.ListedColormap(['#d62728', '#ffea00', '#00ffeb'])
                        norm_suit = mcolors.BoundaryNorm([0.5, 1.5, 2.5, 3.5], cmap_suit.N)
                        ax2.imshow(suit_data, cmap=cmap_suit, norm=norm_suit, interpolation='nearest', alpha=0.8)
                        ax2.set_title('Restoration Suitability Map Overlay', fontsize=14, weight='bold')
                        ax2.axis('off')

                        labels_suit = [
                            'Critical Zone (Drone Seeding Required)',
                            'Moderate Zone (Standard Planting)',
                            'High Readiness (Assisted Natural Restoration)'
                        ]
                        colors_suit = ['#d62728', '#ffea00', '#00ffeb']
                        patches_suit = [mpatches.Patch(color=colors_suit[i], label=labels_suit[i]) for i in range(len(colors_suit))]
                        ax2.legend(handles=patches_suit, loc='lower center', bbox_to_anchor=(0.5, -0.15), ncol=1, fontsize=10)
                        plt.tight_layout()
                        st.pyplot(fig2)

                    with tab4:
                        st.subheader("Phase 3: Micro-Targeting Priority Score (0-100)")
                        fig3, ax3 = plt.subplots(figsize=(12, 7))
                        ax3.imshow(rgb_data, interpolation='bilinear')

                        im_score = ax3.imshow(score_data, cmap='RdYlGn', vmin=0, vmax=100, interpolation='nearest', alpha=0.8)
                        ax3.axis('off')
                        cbar3 = fig3.colorbar(im_score, ax=ax3, fraction=0.046, pad=0.04)
                        cbar3.set_label('Restoration Priority Score (100 = Optimal Condition)', fontsize=12, weight='bold')

                        # กล่องข้อความแบบเรียบไม่มีปัญหา Syntax LaTeX ในบาง Browser
                        props = dict(boxstyle='round', facecolor='white', alpha=0.8)
                        textstr = "Score Classification:\n" \
                                  "• 80-100: Prime Target (Favorable slope, rich soil)\n" \
                                  "• 40-79: Secondary Target (Requires maintenance)\n" \
                                  "• 0-39: Avoid / High Cost (Steep slope, severe erosion)"
                        ax3.text(1.05, 0.5, textstr, transform=ax3.transAxes, fontsize=10, verticalalignment='center', bbox=props)
                        plt.tight_layout()
                        st.pyplot(fig3)

                    st.success("✅ ประมวลผลภาพอินพุตตำแหน่งใหม่เสร็จสมบูรณ์เรียบร้อยแล้ว!")
                else:
                    st.error("❌ ขอบเขตพื้นที่ศึกษาไม่มีข้อมูลพิกัดดาวเทียมตอบรับกลับมา กรุณาลดรัศมีหรือตรวจสอบพิกัด")
        except Exception as e:
            st.error(f"เกิดข้อผิดพลาดในการประมวลผลข้อมูล: {e}")
else:
    st.info("💡 ปรับเปลี่ยนพิกัดละติจูด/ลองจิจูดที่เมนูด้านซ้ายเพื่อป้อน 'ภาพอินพุตใหม่' จากนั้นกดปุ่ม 'ประมวลผล'")
