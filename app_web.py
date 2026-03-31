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

# 🔥 캡처해주신 화면에 맞춰 구글 시트 파일 이름을 정확히 수정했습니다!
GOOGLE_SHEET_NAME = "USP_추출기" 

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

# ==========================================
# [구글 시트 연결] 
# ==========================================
def connect_google_sheet():
    try:
        scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
        creds_dict = dict(st.secrets["gcp_service_account"])
        
        # 암호(PEM) 포맷이 깨지는 것을 방지하는 강력한 보호 코드
        private_key = creds_dict.get("private_key", "")
        private_key = private_key.replace("\\n", "\n").replace('"', '').replace("'", "").strip()
        creds_dict["private_key"] = private_key
            
        creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, scope)
        client = gspread.authorize(creds)
        return client.open(GOOGLE_SHEET_NAME).sheet1
    except Exception as e:
        st.error(f"🚨 구글 시트 연결 실패: {e}")
        return None

def save_to_google_sheet(data_list):
    sheet = connect_google_sheet()
    if sheet: 
        try:
            sheet.append_row(data_list)
        except Exception as e:
            st.error(f"🚨 시트 기록 실패 (이메일 공유 권한을 확인하세요): {e}")

# ==========================================
# [데이터 수집] 🔥 봇 차단 우회(크롬 드라이버 전면 배치)
# ==========================================
def get_data_bulldozer(target_url, product_code, max_pages=50):
    encoded_parent_url = urllib.parse.quote(target_url, safe='')
    crema_api_base = f"https://review4.cre.ma/v2/xexymix.com/product_reviews/list_v3?product_code={product_code}&parent_url={encoded_parent_url}&page="
    brand_text = ""
    review_list = []
    
    options = Options()
    options.binary_location = "/usr/bin/chromium" 
    options.add_argument('--headless') 
    options.add_argument('--no-sandbox') 
    options.add_argument('--disable-dev-shm-usage') 
    options.add_argument('--disable-gpu')
    options.add_argument('user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36')

    # 브라우저를 한 번만 켜서 상세페이지와 리뷰를 모두 수집합니다!
    try:
        service = Service("/usr/bin/chromedriver") 
        driver = webdriver.Chrome(service=service, options=options)
        
        # 1. 상세페이지 수집 (우회)
        status_container.info(f"🚀 (1/3) 상세페이지 설명 수집 중...")
        try:
            driver.get(target_url)
            time.sleep(2)
            brand_text = driver.find_element(By.TAG_NAME, 'body').text.strip()[:5000]
        except Exception as e:
            status_container.warning(f"⚠️ 상세페이지 텍스트 수집 실패: {e}")

        # 2. 리뷰 수집
        status_container.info(f"🤖 (2/3) 클라우드 브라우저를 백그라운드에서 실행하여 리뷰 수집 중...")
        progress_bar = st.progress(0)
        for page in range(1, max_pages + 1):
            driver.get(f"{crema_api_base}{page}")
            time.sleep(2.5)
            content = driver.find_element(By.TAG_NAME, 'body').text.strip()
            if len(content) < 50:
                status_container.info(f"⏹️ {page}페이지에 더 이상 리뷰가 없어 수집을 자동 종료합니다.")
                break
            review_list.append(content)
            progress_bar.progress(page/max_pages)
            
    except Exception as e:
        status_container.error(f"⚠️ 브라우저 실행 오류: {e}")
    finally:
        try:
            driver.quit()
            if 'progress_bar' in locals(): progress_bar.empty()
        except: pass
        
    final_review_text = "\n".join(review_list)[:30000] 
    status_container.success(f"✅ 총 {len(review_list)}페이지 분량의 실제 리뷰를 확보했습니다!")
    return brand_text, final_review_text

# ==========================================
# [AI 요약 및 워드클라우드] 
# ==========================================
def analyze_deep_usp_summarized(brand_text, review_text):
    status_container.info("🧠 (3/3) 제미나이 AI가 바쁜 실무자를 위해 결과를 '초압축 요약' 중입니다...")
    prompt = f"""
    당신은 10년 차 데이터 기반 시니어 퍼포먼스 마케터입니다. 
    바쁜 실무자가 한눈에 파악할 수 있도록 구구절절한 설명은 전부 빼고, **최대한 짧고 명확하게 개조식(불릿 포인트)**으로만 요약하세요.
    
    [출력 양식]
    ### 🏢 1. 브랜드 기획 의도 (상세페이지)
    * 핵심 소구점 3가지 (각 1줄 요약)
    
    ### 🗣️ 2. 고객 진짜 반응 (리뷰 핵심 요약)
    * **극찬 키워드 Top 5**: (예: 핏 보정 - 허리 라인을 확실히 잡아줌)
    * **아쉬운 점 Top 3**: (예: 지퍼 - 올리고 내릴 때 다소 뻑뻑함)
    * **고객의 진짜 구매 이유**: (1문장 요약)
    
    ### 🎯 3. 타겟 페르소나 & 후킹 카피 (초압축)
    * **주요 타겟층**: 리뷰를 바탕으로 한 주요 연령대, 성별, 활동 특성 요약
    * **시즌/타겟 맞춤 카피 제안 (무조건 20자 내외로 짧게!)**:
      1) [불만 해결형]: (예: 부해보이는 집업은 이제 그만!)
      2) [욕망 자극형]: (예: 입는 순간 -3kg, 마법의 슬림핏)
      3) [시즌 맞춤형]: (예: 올봄 야외 러닝, 이거 하나면 끝!)
    
    ==========================
    [데이터 1: 상세페이지]
    {brand_text}
    
    [데이터 2: 고객 리뷰 전량]
    {review_text}
    """
    try:
        client = genai.Client(api_key=MY_GEMINI_API_KEY)
        response = client.models.generate_content(model='gemini-2.5-flash', contents=prompt)
        return response.text
    except Exception as e:
        status_container.error(f"⚠️ AI 분석 실패: {e}")
        return "AI 분석 결과를 가져오지 못했습니다."

def create_wordcloud_summary(review_text):
    try:
        wc_prompt = f"다음 대량의 리뷰에서 가장 중요한 제품 관련 키워드 100개만 뽑아서 나열해줘.\n{review_text[:8000]}"
        client = genai.Client(api_key=MY_GEMINI_API_KEY)
        keywords = client.models.generate_content(model='gemini-2.5-flash', contents=wc_prompt).text
        
        font_path = "NanumGothic.ttf"
        if not os.path.exists(font_path):
            urllib.request.urlretrieve("https://github.com/google/fonts/raw/main/ofl/nanumgothic/NanumGothic-Regular.ttf", font_path)
        
        wordcloud = WordCloud(
            font_path=font_path, width=800, height=800, 
            background_color='white', colormap='magma'
        ).generate(keywords)
        
        img_buffer = io.BytesIO()
        plt.figure(figsize=(8, 8))
        plt.imshow(wordcloud, interpolation='bilinear')
        plt.axis('off')
        plt.savefig(img_buffer, format='png', bbox_inches='tight')
        plt.close()
        
        return img_buffer.getvalue()
    except Exception as e:
        status_container.warning(f"⚠️ 워드클라우드 생성 실패: {e}")
        return None

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
            
            # 🔥 요청하신 안내 문구로 완벽하게 교체되었습니다!
            st.markdown("💡 **분석하고 싶은 제품의 전체 URL 주소 하나만 넣어주세요!**")
            st.markdown("💡 **URL 기재 시 예시와 같이 코드까지만 입력해주세요**<br><span style='font-size:13px;'>(예시: https://www.xexymix.com/shop/shopdetail.html?branduid=2069060)</span>", unsafe_allow_html=True)
            
            main_url_input = st.text_input("🔗 분석할 상품 URL", value="")
            max_pages_input = st.slider("📜 수집 페이지 수", min_value=10, max_value=50, value=30, step=5)
        
        col_btn1, col_btn2, col_btn3 = st.columns([1, 1, 1])
        with col_btn2:
            start_btn = st.button("▶ 분석 시작 및 시트 저장", type="primary", use_container_width=True)

        status_container = st.container()

        if start_btn:
            if not main_url_input: 
                st.warning("⚠️ 분석할 상품의 URL 주소를 먼저 입력해주세요!")
            else:
                parsed_url = urlparse(main_url_input)
                query_params = parse_qs(parsed_url.query)
                
                if 'branduid' not in query_params:
                    st.error("🚨 입력하신 URL에서 상품 고유 번호(branduid)를 찾을 수 없습니다.")
                else:
                    product_code = query_params['branduid'][0]
                    with status_container:
                        brand_txt, review_txt = get_data_bulldozer(main_url_input, product_code, max_pages_input)
                        if len(review_txt) < 50:
                            st.error("🚨 리뷰 수집 실패. 크롬 드라이버가 제대로 실행되지 않았습니다.")
                        else:
                            report = analyze_deep_usp_summarized(brand_txt, review_txt)
                            img = create_wordcloud_summary(review_txt)
                            now_str = datetime.datetime.now().strftime("%Y%m%d_%H%M")
                            
                            save_to_google_sheet([now_str, product_code, main_url_input, report])
                            
                            st.session_state.final_report = report
                            st.session_state.wc_img = img
                            st.session_state.filename_base = f"USP_{product_code}_{now_str}"
                            st.session_state.main_url = main_url_input
                            st.session_state.analyzed = True
                            st.toast("✅ 분석 완료 및 구글 시트 저장 성공!", icon="🎉")

        if st.session_state.analyzed:
            st.markdown("---")
            result_expander = st.expander("📝 1. AI 핵심 요약 분석 결과 (클릭하여 열기)", expanded=True)
            with result_expander:
                st.markdown(st.session_state.final_report)
                st.text_area("📋 결과 복사하기", st.session_state.final_report, height=300)

            wordcloud_expander = st.expander("☁️ 2. 리뷰 키워드 워드클라우드 (클릭하여 열기)", expanded=True)
            with wordcloud_expander:
                if st.session_state.wc_img:
                    st.image(st.session_state.wc_img, caption="리뷰 핵심 키워드")
                else:
                    st.markdown("워드클라우드 이미지를 생성할 수 없습니다.")
            
            col4, col5 = st.columns([1, 1])
            with col4:
                st.download_button(
                    label="💾 요약 보고서(.txt) 다운로드",
                    data=f"분석 대상: {st.session_state.main_url}\n==========================\n\n{st.session_state.final_report}",
                    file_name=f"{st.session_state.filename_base}.txt",
                    mime="text/plain",
                    use_container_width=True
                )
            with col5:
                if st.session_state.wc_img:
                    st.download_button(
                        label="💾 워드클라우드(.png) 다운로드",
                        data=st.session_state.wc_img,
                        file_name=f"{st.session_state.filename_base}.png",
                        mime="image/png",
                        use_container_width=True
                    )

    with tab2:
        st.header("📋 과거 분석 히스토리")
        sheet = connect_google_sheet()
        if sheet:
            data = sheet.get_all_records()
            if data: st.table(data)
            else: st.info("아직 저장된 내역이 없습니다.")

    st.markdown("<br><center>마케팅 자동화 솔루션 | Internal Tool V9.3</center>", unsafe_allow_html=True)
