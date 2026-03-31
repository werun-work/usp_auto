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
# [데이터 수집 엔진] 🔥 강력한 타임아웃 방어 로직 추가
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
        
        # 🔥 15초 안에 안 열리면 강제로 끊어버립니다.
        driver.set_page_load_timeout(15)
        
        status_container.info(f"🚀 (1/3) 상세페이지 설명 수집 중...")
        try:
            driver.get(target_url)
            time.sleep(2)
        except Exception as e:
            # 15초가 지나 타임아웃 에러가 나더라도, 무시하고 진행합니다! (서버 기절 방지)
            try:
                driver.execute_script("window.stop();") # 브라우저 로딩 강제 종료
            except: pass
            status_container.warning("⚠️ 상세페이지 로딩이 길어 강제 중단 후 텍스트만 추출합니다.")

        # 어떻게든 화면에 뜬 글자만 긁어옵니다.
        try:
            brand_text = driver.find_element(By.TAG_NAME, 'body').text.strip()[:5000]
        except:
            brand_text = "상세페이지 텍스트 수집 실패"

        status_container.info(f"🤖 (2/3) 리뷰 수집 경로 분석 및 추출 중...")
        if "xexymix.com" in target_url:
            parsed = urlparse(target_url)
            product_code = parse_qs(parsed.query).get('branduid', [''])[0]
            if product_code:
                encoded_url = urllib.parse.quote(target_url, safe='')
                for page in range(1, max_pages + 1):
                    try:
                        driver.get(f"https://review4.cre.ma/v2/xexymix.com/product_reviews/list_v3?product_code={product_code}&parent_url={encoded_url}&page={page}")
                        time.sleep(2)
                        content = driver.find_element(By.TAG_NAME, 'body').text.strip()
                        if len(content) < 50: break
                        review_list.append(content)
                    except Exception as review_e:
                        # 중간에 한 페이지가 멈춰도 전체가 망가지지 않게 그냥 넘어갑니다(Pass).
                        pass
        else:
            review_list.append(brand_text[1000:4000])
            
    except Exception as e:
        status_container.error(f"⚠️ 브라우저 시스템 오류: {e}")
    finally:
        try:
            driver.quit()
        except: pass
        
    return brand_text, "\n".join(review_list)[:30000]

# ==========================================
# [AI 요약]
# ==========================================
def analyze_deep_usp_summarized(brand_text, review_text):
    status_container.info("🧠 (3/3) 제미나이 AI가 '생활 밀착형 USP 고도화' 전략으로 분석 중입니다...")
    prompt = f"""
    # Role: 시니어 커머스 전략가 (생활 밀착형 라이프스타일 큐레이터)
    # Task: 상세페이지와 리뷰를 분석하여, 중복되지 않는 5가지 관점의 '생활 밀착형 USP'와 '명사/동사 중심' 후킹 카피 추출 및 광고 소재 기획

    # 분석 가이드라인 (USP 고도화 Logic):
    1. 소구점 다각화 (Zero Redundancy): '편안함' 하나에만 매몰되지 않는다. [관리(세탁/다림질), 핏(보정/실루엣), 촉감(피부저자극), 내구성(변형무), 상황(출근/육아/운동)]의 5가지 축으로 USP를 분산 추출한다.
    2. 생활 밀착 (Real Life): "다림질 생략", "세탁기 직행", "건조기 생존" 등 사용자가 제품을 '관리하고 유지하는 과정'에서의 이득을 반드시 포함한다.
    3. 언어의 직관성 (Short & Punchy): 모든 카피는 20자 이내, '명사' 혹은 '동사'로 종결하여 이미지로 즉각 각인시킨다.

    ---
    # Input Data:
    [상세페이지 텍스트]
    {brand_text}
    
    [고객 리뷰 데이터 전량]
    {review_text if len(review_text) > 50 else "현재 수집된 리뷰 데이터가 없습니다."}
    ---

    # Output Format:
    (주의: 각 항목의 설명 문구나 가이드라인은 제외하고 최종 결과 텍스트만 깔끔하게 출력하세요. 수집된 리뷰가 없을 경우 '수집된 리뷰 없음'으로 명시하고 상세페이지 기준으로 유추하여 기획하세요.)

    ### 🏢 1. 5대 다각도 핵심 USP (경험 중심)
    1. **[관리/유지]**: 
    2. **[시각적 핏]**: 
    3. **[물성/촉감]**: 
    4. **[내구성]**: 
    5. **[상황 확산]**: 

    ### 🗣️ 2. 고객의 '진짜 생활' 리뷰 (Pain-Point 중심)
    * **[생활 밀착 키워드 Top 5]**: 
    * **[고객의 한 마디]**: 

    ### 🎯 3. 초압축 다각도 후킹 카피 (명사/동사 중심)
    1. **[관리 혁명형]** 2. **[시간 단축형]** 3. **[시각 보정형]** 4. **[피부 공감형]** 5. **[가성비 증명형]** 6. **[상황 저격형]** 7. **[사회적 증거형]** 8. **[손실 방지형]** ### 💡 4. 소재 제작 기획안 (크리에이티브 한 끗)
    * **[메인 레퍼런스 이미지 기획]**: 위 카피 중 성과가 가장 좋을 것으로 예상되는 '생활 밀착형 이미지' 구도 1가지 제안 (어떤 모델이 어떤 상황에서 무엇을 하고 있는지 구체적으로 시각화)
    
    ### 🎬 5. 숏폼 영상 기획안 (6초~15초)
    * **[초반 Hook (0~3초)]**: 시선을 끄는 문제 제기 또는 극적인 상황
    * **[Body 전개 (3~10초)]**: 제품으로 해결되는 극적인 대비(Before/After) 또는 생활 밀착형 솔루션 시연
    * **[Action 마무리 (10~15초)]**: 구매 유도(CTA) 및 직관적 카피 마무리지기
    """
    try:
        client = genai.Client(api_key=MY_GEMINI_API_KEY)
        response = client.models.generate_content(model='gemini-2.5-flash', contents=prompt)
        return response.text
    except Exception as e:
        return f"AI 분석 실패: {e}"

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
                    
                    if len(review_txt) < 50:
                        img = None
                    else:
                        img = create_wordcloud_summary(review_txt)
                    
                    now = datetime.datetime.now()
                    weekdays = ['월', '화', '수', '목', '금', '토', '일']
                    formatted_date = f"{now.strftime('%Y-%m-%d')}({weekdays[now.weekday()]})"
                    now_str = now.strftime("%Y%m%d_%H%M")
                    
                    parsed = urlparse(main_url_input)
                    qs = parse_qs(parsed.query)
                    p_code = qs.get('branduid', qs.get('product_no', ['UNKNOWN']))[0]
                    
                    save_to_google_sheet([formatted_date, p_code, main_url_input, report], worker_input)
                    
                    st.session_state.final_report = report
                    st.session_state.wc_img = img
                    st.session_state.filename_base = f"USP_{p_code}_{now_str}"
                    st.session_state.main_url = main_url_input
                    st.session_state.worker_name = worker_input
                    st.session_state.analyzed = True
                    st.toast("✅ 분석 완료!", icon="🎉")

        if st.session_state.analyzed:
            st.markdown("---")
            result_expander = st.expander("📝 1. AI 마케팅 기획안 & 카피 (클릭하여 열기)", expanded=True)
            with result_expander:
                st.markdown(st.session_state.final_report)
                st.text_area("📋 결과 복사하기", st.session_state.final_report, height=400)

            wordcloud_expander = st.expander("☁️ 2. 리뷰 키워드 워드클라우드", expanded=True)
            with wordcloud_expander:
                if st.session_state.wc_img:
                    st.image(st.session_state.wc_img, caption="리뷰 핵심 키워드")
                else:
                    st.markdown("수집된 리뷰가 없어 워드클라우드를 제공하지 않습니다.")
            
            col4, col5 = st.columns([1, 1])
            with col4:
                st.download_button(
                    label="💾 기획안(.txt) 다운로드",
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
            selected_sheet = st.selectbox("📂 조회할 작업자 탭 선택", [ws.title for ws in worksheets])
            if selected_sheet:
                st.dataframe(spreadsheet.worksheet(selected_sheet).get_all_records(), use_container_width=True)

    st.markdown("<br><center>마케팅 자동화 솔루션 | Internal Tool V10.1</center>", unsafe_allow_html=True)
