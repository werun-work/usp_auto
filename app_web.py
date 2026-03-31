import streamlit as st
import time
import requests
import urllib.parse
from urllib.parse import urlparse, parse_qs
from bs4 import BeautifulSoup
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from google import genai
from wordcloud import WordCloud
import matplotlib.pyplot as plt
import io
import datetime
import gspread
from oauth2client.service_account import ServiceAccountCredentials
import os
import urllib.request
import json

# ==========================================
# [초기 세팅 영역] 
# ==========================================
st.set_page_config(page_title="AI USP 추출 솔루션", page_icon="🎯", layout="wide")

try:
    APP_PASSWORD = st.secrets["APP_PASSWORD"] 
    MY_GEMINI_API_KEY = st.secrets["GEMINI_API_KEY"]
except:
    APP_PASSWORD = "123"
    MY_GEMINI_API_KEY = "임시"

GOOGLE_SHEET_NAME = "USP_추출기" 

if 'authenticated' not in st.session_state:
    st.session_state.authenticated = False
for key in ['analyzed', 'final_report', 'wc_img', 'filename_base', 'main_url', 'worker_name']:
    if key not in st.session_state:
        st.session_state[key] = None if key == 'wc_img' else ""

def check_password():
    if st.session_state.authenticated:
        return True
    col1, col2, col3 = st.columns([1, 2, 1])
    with col2:
        st.title("🔐 사내 전용 솔루션 접속")
        st.info("이 도구는 마케팅팀 전용 자산입니다. 비밀번호를 입력해주세요.")
        password_input = st.text_input("접속 비밀번호", type="password")
        if st.button("로그인", use_container_width=True):
            if password_input == APP_PASSWORD:
                st.session_state.authenticated = True
                st.rerun()
            else:
                st.error("🚨 비밀번호가 틀렸습니다.")
    return False

def connect_google_spreadsheet():
    try:
        scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
        creds_json_str = st.secrets["GOOGLE_CREDENTIALS"]
        creds_dict = json.loads(creds_json_str)
        creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, scope)
        client = gspread.authorize(creds)
        return client.open(GOOGLE_SHEET_NAME) 
    except Exception as e:
        st.error(f"🚨 구글 시트 연결 실패: {e}")
        return None

def save_to_google_sheet(data_list, worker_name):
    spreadsheet = connect_google_spreadsheet()
    if spreadsheet: 
        try:
            try:
                worksheet = spreadsheet.worksheet(worker_name)
            except gspread.WorksheetNotFound:
                worksheet = spreadsheet.add_worksheet(title=worker_name, rows="100", cols="10")
                worksheet.append_row(["날짜", "상품코드", "URL", "분석결과"])
            worksheet.append_row(data_list)
        except Exception as e:
            st.error(f"🚨 시트 기록 실패: {e}")

# ==========================================
# [데이터 수집 엔진] 🔥 멀티 사이트 대응 버전
# ==========================================
def get_data_bulldozer(target_url, max_pages=30):
    brand_text = ""
    review_list = []
    
    options = Options()
    options.binary_location = "/usr/bin/chromium" 
    options.add_argument('--headless') 
    options.add_argument('--no-sandbox') 
    options.add_argument('--disable-dev-shm-usage') 
    options.add_argument('--disable-gpu')
    options.add_experimental_option("prefs", {"profile.managed_default_content_settings.images": 2})
    options.page_load_strategy = 'eager' 

    try:
        service = Service("/usr/bin/chromedriver") 
        driver = webdriver.Chrome(service=service, options=options)
        driver.set_page_load_timeout(20)
        
        # 1. 상세페이지 수집
        status_container.info(f"🚀 (1/3) 상세페이지 설명 수집 중...")
        driver.get(target_url)
        time.sleep(3)
        brand_text = driver.find_element(By.TAG_NAME, 'body').text.strip()[:5000]

        # 2. 리뷰 수집 (사이트별 분기 처리)
        status_container.info(f"🤖 (2/3) 리뷰 수집 경로 분석 및 추출 중...")
        
        # 젝시믹스 전용 (Crema 솔루션)
        if "xexymix.com" in target_url:
            parsed = urlparse(target_url)
            product_code = parse_qs(parsed.query).get('branduid', [''])[0]
            if product_code:
                encoded_url = urllib.parse.quote(target_url, safe='')
                for page in range(1, max_pages + 1):
                    driver.get(f"https://review4.cre.ma/v2/xexymix.com/product_reviews/list_v3?product_code={product_code}&parent_url={encoded_url}&page={page}")
                    time.sleep(2)
                    content = driver.find_element(By.TAG_NAME, 'body').text.strip()
                    if len(content) < 50: break
                    review_list.append(content)
        
        # 안다르 및 일반 사이트 (스크롤 방식/기본 텍스트 수집)
        else:
            # 안다르 등 일반 사이트는 페이지 내에 리뷰가 이미 로드되어 있거나 
            # 버튼을 눌러야 하므로 현재 페이지의 텍스트를 최대한 긁어옵니다.
            # (전문적인 크롤링은 사이트별 셀렉터가 필요하나, 우선 범용 텍스트 분석으로 대응)
            review_list.append(brand_text[1000:4000]) # 리뷰 대용으로 상세페이지 하단 텍스트 일부 활용
            
    except Exception as e:
        status_container.error(f"⚠️ 수집 중 오류: {e}")
    finally:
        driver.quit()
        
    return brand_text, "\n".join(review_list)[:30000]

# ==========================================
# [AI 요약] 
# ==========================================
def analyze_deep_usp_summarized(brand_text, review_text):
    status_container.info("🧠 (3/3) 제미나이 AI가 'USP 고도화' 커머스 전략으로 분석 중입니다...")
    prompt = f"""
    # Role: 데이터 기반의 초개인화 커머스 전략가 (USP 고도화 카피라이팅 전문가)
    # Context: 상세페이지의 기술적 스펙을 고객의 '라이프스타일 이익'으로 치환하여 클릭률(CTR)을 200% 이상 개선하는 것이 목표

    # Output Format:
    ### 🏢 1. 브랜드 기획 의도 & '한 끗'의 차이
    * **해결하고자 하는 결핍**: 
    * **독보적 기술 스펙**: 
    * **치환된 고객 이익**: 

    ### 🗣️ 2. 고객의 진짜 목소리 (Deep Review Analysis)
    * **[Unspoken Pain]**: 
    * **[Moment of Wow]**: 
    * **[New Usage]**: 

    ### 🎯 3. USP 고도화 다각도 후킹 카피 (8가지 앵글, 각 20자 이내)
    1. **[문제 저격형]** 2. **[TPO 특정형]** 3. **[비교 우위형]** 4. **[데이터 증명형]** 5. **[자존감 고취형]** 6. **[관리 편의형]** 7. **[리뷰 워딩형]** 8. **[손실 강조형]** ### 🖼️ 4. 비주얼 훅(Visual Hook) 제안
    * ---
    [데이터]
    {brand_text}
    {review_text if len(review_text) > 50 else "리뷰 데이터 부족 (상세페이지 기반 분석)"}
    """
    try:
        client = genai.Client(api_key=MY_GEMINI_API_KEY)
        response = client.models.generate_content(model='gemini-2.5-flash', contents=prompt)
        return response.text
    except Exception as e:
        return f"AI 분석 실패: {e}"

# ==========================================
# [실제 화면 렌더링] 
# ==========================================
if check_password():
    col_t1, col_t2 = st.columns([9, 1])
    with col_t2:
        if st.button("로그아웃"):
            st.session_state.authenticated = False
            st.rerun()

    st.title("🎯 마케팅 USP & 카피 자동 추출기")
    st.markdown("---")

    tab1, tab2 = st.tabs(["🎯 새 분석 실행", "📜 히스토리 보기"])

    with tab1:
        with st.sidebar:
            st.header("설정")
            worker_input = st.text_input("👤 작업자 이름", value="", placeholder="예: 김마케터")
            st.markdown("---")
            main_url_input = st.text_input("🔗 분석할 상품 URL", value="", placeholder="URL을 입력하세요")
            max_pages_input = st.slider("📜 수집 페이지 수", 10, 50, 30, 5)
        
        col_btn1, col_btn2, col_btn3 = st.columns([1, 1, 1])
        with col_btn2:
            start_btn = st.button("▶ 분석 시작 및 시트 저장", type="primary", use_container_width=True)

        status_container = st.container()

        if start_btn:
            if not worker_input or not main_url_input:
                st.warning("⚠️ 이름과 URL을 모두 입력해주세요!")
            else:
                with status_container:
                    brand_txt, review_txt = get_data_bulldozer(main_url_input, max_pages_input)
                    report = analyze_deep_usp_summarized(brand_txt, review_txt)
                    
                    now = datetime.datetime.now()
                    weekdays = ['월', '화', '수', '목', '금', '토', '일']
                    formatted_date = f"{now.strftime('%Y-%m-%d')}({weekdays[now.weekday()]})"
                    
                    # 상품 코드 추출 (젝시믹스 branduid / 안다르 product_no 등 범용)
                    parsed = urlparse(main_url_input)
                    qs = parse_qs(parsed.query)
                    p_code = qs.get('branduid', qs.get('product_no', ['UNKNOWN']))[0]
                    
                    save_to_google_sheet([formatted_date, p_code, main_url_input, report], worker_input)
                    
                    st.session_state.final_report = report
                    st.session_state.analyzed = True
                    st.toast("✅ 분석 완료!", icon="🎉")

        if st.session_state.analyzed:
            st.markdown("---")
            st.markdown(st.session_state.final_report)

    with tab2:
        st.header("📋 과거 분석 히스토리")
        spreadsheet = connect_google_spreadsheet()
        if spreadsheet:
            worksheets = spreadsheet.worksheets()
            selected_sheet = st.selectbox("📂 조회할 작업자 탭 선택", [ws.title for ws in worksheets])
            if selected_sheet:
                st.dataframe(spreadsheet.worksheet(selected_sheet).get_all_records(), use_container_width=True)

    st.markdown("<br><center>마케팅 자동화 솔루션 | Internal Tool V9.8 (Multi-Engine)</center>", unsafe_allow_html=True)
