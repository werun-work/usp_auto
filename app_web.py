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

st.set_page_config(page_title="AI USP 추출 솔루션", page_icon="🔐", layout="wide")

try:
    APP_PASSWORD = st.secrets["APP_PASSWORD"] 
    MY_GEMINI_API_KEY = st.secrets["GEMINI_API_KEY"]
except:
    st.warning("⚠️ 클라우드 보안 금고(Secrets)가 아직 설정되지 않았습니다.")
    APP_PASSWORD = "123"
    MY_GEMINI_API_KEY = "임시"

GOOGLE_SHEET_NAME = "마케팅_분석_히스토리" 

if 'authenticated' not in st.session_state:
    st.session_state.authenticated = False
for key in ['analyzed', 'final_report', 'wc_img', 'filename_base', 'main_url']:
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

def connect_google_sheet():
    try:
        scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
        creds_dict = dict(st.secrets["gcp_service_account"])
        creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, scope)
        client = gspread.authorize(creds)
        return client.open(GOOGLE_SHEET_NAME).sheet1
    except Exception as e:
        st.error(f"🚨 구글 시트 연결 실패: {e}")
        return None

def save_to_google_sheet(data_list):
    sheet = connect_google_sheet()
    if sheet: sheet.append_row(data_list)

def get_data_bulldozer(target_url, product_code, max_pages=50):
    encoded_parent_url = urllib.parse.quote(target_url, safe='')
    crema_api_base = f"https://review4.cre.ma/v2/xexymix.com/product_reviews/list_v3?product_code={product_code}&parent_url={encoded_parent_url}&page="
    brand_text = ""
    try:
        headers = {'User-Agent': 'Mozilla/5.0'}
        res = requests.get(target_url, headers=headers)
        soup = BeautifulSoup(res.text, 'html.parser')
        brand_text = soup.get_text(separator=' ', strip=True)[:5000]
    except: pass
    
    review_list = []
    
    # 🔥 클라우드 전용 절대 경로 세팅
    options = Options()
    options.binary_location = "/usr/bin/chromium" 
    options.add_argument('--headless') 
    options.add_argument('--no-sandbox') 
    options.add_argument('--disable-dev-shm-usage') 
    options.add_argument('--disable-gpu')
    options.add_argument('user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36')

    try:
        service = Service("/usr/bin/chromedriver") 
        driver = webdriver.Chrome(service=service, options=options)
        
        p_bar = st.progress(0)
        for page in range(1, max_pages + 1):
            driver.get(f"{crema_api_base}{page}")
            time.sleep(2.5)
            content = driver.find_element(By.TAG_NAME, 'body').text.strip()
            if len(content) < 50: break
            review_list.append(content)
            p_bar.progress(page/max_pages)
    except Exception as e:
        st.error(f"⚠️ 브라우저 수집 오류: {e}")
    finally:
        try:
            driver.quit()
        except: pass
        
    return brand_text, "\n".join(review_list)

def analyze_deep_usp_summarized(brand_text, review_text):
    client = genai.Client(api_key=MY_GEMINI_API_KEY)
    prompt = f"마케팅 전문가로서 다음 데이터를 초압축 요약하세요. [상세의도], [긍정Top5], [부정Top3], [20자카피3종] 양식 준수.\n\n데이터:{brand_text}\n{review_text}"
    response = client.models.generate_content(model='gemini-2.5-flash', contents=prompt)
    return response.text

if check_password():
    col_t1, col_t2 = st.columns([9, 1])
    with col_t2:
        if st.button("로그아웃"):
            st.session_state.authenticated = False
            st.rerun()

    st.title("🎯 마케팅 USP 자동 추출기 (Cloud Pro)")
    st.markdown("---")

    tab1, tab2 = st.tabs(["🎯 새 분석 실행", "📜 히스토리 보기"])

    with tab1:
        with st.sidebar:
            st.header("설정")
            main_url_input = st.text_input("🔗 분석할 상품 URL", "https://www.xexymix.com/shop/shopdetail.html?branduid=2069060")
            max_pages_input = st.slider("📜 수집 페이지 수", 10, 50, 30)
        
        col_btn1, col_btn2, col_btn3 = st.columns([1, 1, 1])
        with col_btn2:
            start_btn = st.button("▶ 분석 시작 및 시트 저장", type="primary", use_container_width=True)

        status_container = st.container()

        if start_btn:
            parsed_url = urlparse(main_url_input)
            product_code = parse_qs(parsed_url.query).get('branduid', [''])[0]
            
            if product_code:
                with status_container:
                    brand_txt, review_txt = get_data_bulldozer(main_url_input, product_code, max_pages_input)
                    if len(review_txt) < 50:
                        st.error("🚨 리뷰 수집 실패. 크롬 드라이버가 제대로 실행되지 않았습니다.")
                    else:
                        report = analyze_deep_usp_summarized(brand_txt, review_txt)
                        now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
                        save_to_google_sheet([now, product_code, main_url_input, report])
                        
                        st.session_state.final_report = report
                        st.session_state.analyzed = True
                        st.session_state.main_url = main_url_input
                        st.toast("✅ 분석 완료 및 구글 시트 저장 성공!", icon="🎉")

        if st.session_state.analyzed:
            st.markdown("---")
            st.markdown(st.session_state.final_report)
            st.download_button("💾 결과 다운로드 (.txt)", st.session_state.final_report, file_name="USP_result.txt")

    with tab2:
        st.header("📋 과거 분석 히스토리")
        sheet = connect_google_sheet()
        if sheet:
            data = sheet.get_all_records()
            if data: st.table(data)
            else: st.info("아직 저장된 내역이 없습니다.")
