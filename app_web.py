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

# ==========================================
# [구글 시트 연결]
# ==========================================
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
# [데이터 수집] 
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
    
    prefs = {"profile.managed_default_content_settings.images": 2,
             "profile.default_content_setting_values.notifications": 2,
             "profile.managed_default_content_settings.stylesheets": 2,
             "profile.managed_default_content_settings.cookies": 2,
             "profile.managed_default_content_settings.plugins": 2,
             "profile.managed_default_content_settings.geolocation": 2,
             "profile.managed_default_content_settings.media_stream": 2,
             }
    options.add_experimental_option("prefs", prefs)
    options.page_load_strategy = 'eager' 

    try:
        service = Service("/usr/bin/chromedriver") 
        driver = webdriver.Chrome(service=service, options=options)
        driver.set_page_load_timeout(15) 
        
        status_container.info(f"🚀 (1/3) 상세페이지 설명 수집 중...")
        try:
            driver.get(target_url)
            time.sleep(2)
            brand_text = driver.find_element(By.TAG_NAME, 'body').text.strip()[:5000]
        except Exception as e:
            status_container.warning(f"⚠️ 상세페이지 텍스트 수집 시간 초과. 기본 정보만 수집합니다.")

        status_container.info(f"🤖 (2/3) 리뷰 데이터 확인 및 수집 중...")
        progress_bar = st.progress(0)
        for page in range(1, max_pages + 1):
            try:
                driver.get(f"{crema_api_base}{page}")
                time.sleep(2)
                content = driver.find_element(By.TAG_NAME, 'body').text.strip()
                # 리뷰가 없거나 끝났으면 바로 종료하고 다음 단계로 부드럽게 넘어갑니다.
                if len(content) < 50:
                    if page == 1:
                        status_container.info(f"💡 아직 등록된 리뷰가 없는 상품입니다. 상세페이지 기반으로만 분석을 진행합니다.")
                    else:
                        status_container.info(f"⏹️ {page}페이지에 더 이상 리뷰가 없어 수집을 자동 종료합니다.")
                    break
                review_list.append(content)
            except Exception as e:
                pass
            progress_bar.progress(page/max_pages)
            
    except Exception as e:
        status_container.error(f"⚠️ 브라우저 실행 오류: {e}")
    finally:
        try:
            driver.quit()
            if 'progress_bar' in locals(): progress_bar.empty()
        except: pass
        
    final_review_text = "\n".join(review_list)[:30000] 
    if len(review_list) > 0:
        status_container.success(f"✅ 총 {len(review_list)}페이지 분량의 실제 리뷰를 확보했습니다!")
    return brand_text, final_review_text

# ==========================================
# [AI 요약] 🔥 특정 브랜드 지칭 제거, 서브타이틀 제거, USP 고도화 반영
# ==========================================
def analyze_deep_usp_summarized(brand_text, review_text):
    status_container.info("🧠 (3/3) 제미나이 AI가 'USP 고도화' 커머스 전략으로 분석 중입니다...")
    prompt = f"""
    # Role: 데이터 기반의 초개인화 커머스 전략가 (USP 고도화 카피라이팅 전문가)
    # Context: 상세페이지의 기술적 스펙을 고객의 '라이프스타일 이익'으로 치환하여 클릭률(CTR)을 200% 이상 개선하는 것이 목표

    # 분석 로직 (USP 고도화 Framework):
    1. 마이크로 페인포인트(Micro-Painpoint): 고객이 스스로도 인지하지 못했던 '한 끗'의 불편함을 찾아낸다.
    2. 기술의 일상화: 어려운 소재 설명을 일상적 표현으로 바꾼다.
    3. 결핍의 시각화: 이 제품이 없을 때 겪는 민망함이나 불편함을 시각적으로 묘사한다.
    4. 결과적 감정(Emotion): 제품을 사용한 후 고객이 느낄 '자존감'이나 '해방감'에 집중한다.

    ---
    # Input Data:
    [상세페이지 텍스트]
    {brand_text}
    
    [고객 리뷰 데이터 전량]
    {review_text if len(review_text) > 50 else "현재 수집된 리뷰 데이터가 없습니다."}
    ---

    # Output Format:
    (주의: 고객 리뷰 데이터가 '없음'인 경우, 2번 리뷰 항목은 '수집된 리뷰가 없습니다'라고 기재하고, 나머지 항목은 상세페이지를 기반으로 유추하여 작성하세요.)

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
    * """
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
            
            st.markdown("💡 **담당자 이름을 입력해주세요 (시트 탭 구분용)**")
            worker_input = st.text_input("👤 작업자 이름", value="", placeholder="예: 김마케터")
            st.markdown("---")
            
            st.markdown("💡 **분석하고 싶은 제품의 전체 URL 주소 하나만 넣어주세요!**")
            st.markdown("💡 **URL 기재 시 예시와 같이 코드까지만 입력해주세요**<br><span style='font-size:13px;'>(예시: https://www.xexymix.com/shop/shopdetail.html?branduid=2069060)</span>", unsafe_allow_html=True)
            
            main_url_input = st.text_input("🔗 분석할 상품 URL", value="")
            max_pages_input = st.slider("📜 수집 페이지 수", min_value=10, max_value=50, value=30, step=5)
        
        col_btn1, col_btn2, col_btn3 = st.columns([1, 1, 1])
        with col_btn2:
            start_btn = st.button("▶ 분석 시작 및 시트 저장", type="primary", use_container_width=True)

        status_container = st.container()

        if start_btn:
            if not worker_input:
                st.warning("⚠️ 작업자 이름을 먼저 입력해주세요! (구글 시트 탭 생성을 위해 필수입니다)")
            elif not main_url_input: 
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
                        
                        # 🔥 에러로 멈추지 않고, 무조건 AI 분석으로 넘어갑니다.
                        report = analyze_deep_usp_summarized(brand_txt, review_txt)
                        
                        # 리뷰가 없을 경우 워드클라우드 생성 생략
                        if len(review_txt) < 50:
                            img = None
                            st.info("💡 리뷰가 없는 상품이라 워드클라우드 이미지는 생성하지 않았습니다.")
                        else:
                            img = create_wordcloud_summary(review_txt)
                        
                        now = datetime.datetime.now()
                        weekdays = ['월', '화', '수', '목', '금', '토', '일']
                        formatted_date = f"{now.strftime('%Y-%m-%d')}({weekdays[now.weekday()]})"
                        now_str = now.strftime("%Y%m%d_%H%M")
                        
                        save_to_google_sheet([formatted_date, product_code, main_url_input, report], worker_input)
                        
                        st.session_state.final_report = report
                        st.session_state.wc_img = img
                        st.session_state.filename_base = f"USP_{product_code}_{now_str}"
                        st.session_state.main_url = main_url_input
                        st.session_state.worker_name = worker_input
                        st.session_state.analyzed = True
                        st.toast(f"✅ 분석 완료! 구글 시트 [{worker_input}] 탭에 저장되었습니다.", icon="🎉")

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
                    st.markdown("현재 상품은 수집된 리뷰가 없어 워드클라우드를 제공하지 않습니다.")
            
            col4, col5 = st.columns([1, 1])
            with col4:
                st.download_button(
                    label="💾 요약 보고서(.txt) 다운로드",
                    data=f"분석 대상: {st.session_state.main_url}\n작업자: {st.session_state.worker_name}\n==========================\n\n{st.session_state.final_report}",
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
        spreadsheet = connect_google_spreadsheet()
        if spreadsheet:
            worksheets = spreadsheet.worksheets()
            sheet_names = [ws.title for ws in worksheets]
            
            selected_sheet = st.selectbox("📂 조회할 작업자 탭 선택", sheet_names)
            
            if selected_sheet:
                worksheet = spreadsheet.worksheet(selected_sheet)
                data = worksheet.get_all_records()
                if data: 
                    st.dataframe(data, use_container_width=True) 
                else: 
                    st.info(f"[{selected_sheet}] 탭에 아직 저장된 분석 내역이 없습니다.")

    st.markdown("<br><center>마케팅 자동화 솔루션 | Internal Tool V9.7</center>", unsafe_allow_html=True)
