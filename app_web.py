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
import re
from PIL import Image, ImageDraw, ImageFont

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
for key in ['analyzed', 'final_report', 'wc_img', 'ad_img', 'filename_base', 'main_url', 'worker_name', 'content_type']:
    if key not in st.session_state:
        st.session_state[key] = None if 'img' in key else ""

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
# [데이터 수집 엔진] 🔥 이미지 수집 로직 대폭 수정 (모든 이미지 URL 스캔)
# ==========================================
def get_data_bulldozer(target_url, max_pages=30):
    brand_text = ""
    review_list = []
    
    # 🔥 [중요] 상세페이지 내의 잠재적인 상품 이미지 URL 리스트를 담을 곳
    potential_product_imgs = [] 
    
    status_container.info(f"🚀 (1/3) 대상 서버 접속 및 데이터 수집 준비 중...")
    
    options = Options()
    options.binary_location = "/usr/bin/chromium" 
    options.add_argument('--headless') 
    options.add_argument('--no-sandbox') 
    options.add_argument('--disable-dev-shm-usage') 
    options.add_argument('--disable-gpu')
    options.add_experimental_option("prefs", {"profile.managed_default_content_settings.images": 2})
    options.page_load_strategy = 'eager' 

    try:
        # 1. 상세페이지 HTML 스캔 및 모든 이미지 URL 추출 (로고 필터링 1차 진행)
        try:
            res = requests.get(target_url, headers={'User-Agent': 'Mozilla/5.0'}, timeout=10)
            soup = BeautifulSoup(res.text, 'html.parser')
            
            # og:image 확인
            og_img = soup.find('meta', property='og:image')
            if og_img and og_img.get('content'):
                og_src = og_img['content']
                if 'logo' not in og_src.lower(): # 파일명에 logo가 명시된 것만 배제
                    potential_product_imgs.append(og_src)
                    
            # 본문에 있는 모든 이미지 img 태그 수집
            for img in soup.find_all('img'):
                src = img.get('src', '') or img.get('data-src', '')
                if not src: continue
                
                src_lower = src.lower()
                # 로고, 아이콘, 버튼 등 잡다한 요소는 1차적으로 배제 (AI 토큰 낭비 방지)
                if any(x in src_lower for x in ['logo', 'icon', 'btn', 'button', '.gif', 'blank']):
                    continue
                    
                # 절대 경로로 변환
                if src.startswith('//'):
                    src = 'https:' + src
                elif src.startswith('/'):
                    parsed_uri = urlparse(target_url)
                    src = '{uri.scheme}://{uri.netloc}'.format(uri=parsed_uri) + src
                    
                if src not in potential_product_imgs:
                    potential_product_imgs.append(src)
            
            # AI에게 넘겨줄 이미지 URL 리스트 갯수 제한 (토큰 용량 문제 방지)
            potential_product_imgs = potential_product_imgs[:20]
                
        except Exception as e:
            pass 

        service = Service("/usr/bin/chromedriver") 
        driver = webdriver.Chrome(service=service, options=options)
        driver.set_page_load_timeout(15)
        
        status_container.info(f"🚀 (1/3) 상세페이지 텍스트 수집 중...")
        try:
            driver.get(target_url)
            time.sleep(2)
        except Exception as e:
            try: driver.execute_script("window.stop();") 
            except: pass
            
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
                    except: pass
        else:
            review_list.append(brand_text[1000:4000])
            
    except Exception as e:
        status_container.error(f"⚠️ 브라우저 시스템 오류: {e}")
    finally:
        try: driver.quit()
        except: pass
        
    return brand_text, "\n".join(review_list)[:30000], potential_product_imgs # 🔥 URL 리스트를 그대로 반환합니다.

# ==========================================
# [AI 요약 & 이미지 합성 로직] 🔥 V10.6: AI 이미지 선별 로직 도입
# ==========================================
def analyze_deep_usp_summarized(brand_text, review_text, potential_imgs, content_type):
    status_container.info(f"🧠 (3/3) 제미나이 AI가 '{content_type}' 맞춤형 전략으로 분석 중입니다...")
    
    base_prompt = f"""
    # Role: 시니어 커머스 전략가 (생활 밀착형 라이프스타일 큐레이터)
    
    # 분석 가이드라인 (매우 중요):
    1. **절대 축약 금지**: 리뷰 분석(2번)과 기획안 파트는 분량을 줄이지 말고 구체적이고 디테일하게 작성할 것. 
    2. **카피만 짧게**: 오직 [3번 후킹 카피] 파트만 각 항목을 20자 이내로 명사/동사형으로 짧게 끊어 칠 것.

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

    ### 🗣️ 2. 고객의 '진짜 생활' 리뷰 (Pain-Point 중심, 풍부하게 작성)
    * **[생활 밀착 키워드 Top 5]**: 
    * **[고객의 한 마디]**: 
    * **[해결된 불편함]**: 

    ### 🎯 3. 초압축 다각도 후킹 카피 (각 20자 이내, 명사/동사 중심)
    1. **[관리 혁명형]** 2. **[시간 단축형]** 3. **[시각 보정형]** 4. **[피부 공감형]** 5. **[가성비 증명형]** 6. **[상황 저격형]** 7. **[사회적 증거형]** 8. **[손실 방지형]** """

    # 🔥 이미지 기획 프롬프트에 'AI 이미지 선별' 명령어 추가
    image_prompt = ""
    if "이미지" in content_type:
        image_prompt = f"""
    ### 💡 4. 소재 제작 기획안 (크리에이티브 한 끗, 구체적으로 묘사)
    * **[메인 레퍼런스 이미지 기획]**: 위 카피 중 성과가 가장 좋을 것으로 예상되는 '생활 밀착형 이미지' 구도 상세 제안
    
    # [매우 중요] 광고 이미지 시안 제작을 위한 최적의 이미지 URL 선별:
    아래 [Potential Product Images] 리스트를 확인하고, 로고나 UI 요소가 아닌, **실제 제품이 가장 잘 드러난 사진**의 URL을 딱 하나만 골라주세요. 만약 리스트에 로고밖에 없다면 [SELECTED_IMAGE_URL]None[/SELECTED_IMAGE_URL]이라고 출력하세요.
    
    [BEST_COPY]여기에 3번 카피 중 최고점 카피 1개를 20자 이내로 적어주세요[/BEST_COPY]
    
    [Potential Product Images]
    {json.dumps(potential_imgs)}
    """

    video_prompt = ""
    if "영상" in content_type:
        video_prompt = """
    ### 🎬 5. 숏폼 영상 기획안 (6초~15초 콘티)
    * **[초반 Hook (0~3초)]**: (구체적 상황 묘사)
    * **[Body 전개 (3~10초)]**: (시각적 대비 및 증명)
    * **[Action 마무리 (10~15초)]**: (구매 유도)
    """

    final_prompt = base_prompt + image_prompt + video_prompt

    try:
        client = genai.Client(api_key=MY_GEMINI_API_KEY)
        response = client.models.generate_content(model='gemini-2.5-flash', contents=final_prompt)
        return response.text
    except Exception as e:
        return f"AI 분석 실패: {e}"

def create_ad_image(img_url, best_copy):
    if not img_url or not best_copy or img_url == "None": 
        return None
    try:
        req = urllib.request.Request(img_url, headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req) as response:
            img_data = response.read()
        img = Image.open(io.BytesIO(img_data)).convert("RGB")

        base_width = 1080
        w_percent = (base_width / float(img.size[0]))
        h_size = int((float(img.size[1]) * float(w_percent)))
        img = img.resize((base_width, h_size), Image.Resampling.LANCZOS)

        draw = ImageDraw.Draw(img, 'RGBA')
        box_height = 250
        draw.rectangle(((0, h_size - box_height), (base_width, h_size)), fill=(0, 0, 0, 180))

        font_path = "NanumGothicBold.ttf"
        if not os.path.exists(font_path):
            urllib.request.urlretrieve("https://github.com/google/fonts/raw/main/ofl/nanumgothic/NanumGothic-Bold.ttf", font_path)
        font = ImageFont.truetype(font_path, 55)
        
        text_bbox = draw.textbbox((0, 0), best_copy, font=font)
        text_w = text_bbox[2] - text_bbox[0]
        text_x = (base_width - text_w) / 2
        text_y = h_size - (box_height / 2) - 30
        
        draw.text((text_x, text_y), best_copy, font=font, fill=(255, 255, 255, 255))
        draw.text((text_x, text_y - 60), "🔥 BEST COPY", font=ImageFont.truetype(font_path, 30), fill=(255, 200, 0, 255))

        img_buffer = io.BytesIO()
        img.save(img_buffer, format="PNG")
        return img_buffer.getvalue()
    except: return None

def create_wordcloud_summary(review_text):
    try:
        wc_prompt = f"다음 대량의 리뷰에서 가장 중요한 제품 관련 키워드 100개만 뽑아서 나열해줘.\n{review_text[:8000]}"
        client = genai.Client(api_key=MY_GEMINI_API_KEY)
        keywords = client.models.generate_content(model='gemini-2.5-flash', contents=wc_prompt).text
        
        font_path = "NanumGothic.ttf"
        if not os.path.exists(font_path):
            urllib.request.urlretrieve("https://github.com/google/fonts/raw/main/ofl/nanumgothic/NanumGothic-Regular.ttf", font_path)
        
        wordcloud = WordCloud(font_path=font_path, width=800, height=800, background_color='white', colormap='magma').generate(keywords)
        img_buffer = io.BytesIO()
        plt.figure(figsize=(8, 8))
        plt.imshow(wordcloud, interpolation='bilinear')
        plt.axis('off')
        plt.savefig(img_buffer, format='png', bbox_inches='tight')
        plt.close()
        return img_buffer.getvalue()
    except: return None

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
            
            content_type_input = st.selectbox("🎬 기획안 타겟 선택", ["이미지+영상", "이미지", "영상"], index=0, help="원하는 매체에 맞춰 최적화된 기획안을 도출합니다.")
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
                    # 🔥 이제 potential_imgs는 URL 리스트를 받아옵니다.
                    brand_txt, review_txt, potential_imgs = get_data_bulldozer(main_url_input, max_pages_input)
                    
                    # 🔥 AI 분석 함수에 이미지 URL 리스트를 함께 넘겨줍니다.
                    raw_report = analyze_deep_usp_summarized(brand_txt, review_txt, potential_imgs, content_type_input)
                    
                    best_copy_text = "상품의 매력을 돋보이게 하는 한 줄"
                    selected_img_url = None
                    clean_report = raw_report
                    ad_img = None
                    
                    # '이미지'가 포함된 경우에만 합성 로직을 태웁니다.
                    if "이미지" in content_type_input:
                        # BEST_COPY 추출
                        best_copy_match = re.search(r'\[BEST_COPY\](.*?)\[/BEST_COPY\]', raw_report, re.DOTALL)
                        if best_copy_match:
                            best_copy_text = best_copy_match.group(1).strip()
                        
                        # 🔥 AI가 선별한 SELECTED_IMAGE_URL 추출 (프롬프트에서 출력하도록 수정함)
                        selected_img_match = re.search(r'\[SELECTED_IMAGE_URL\](.*?)\[/SELECTED_IMAGE_URL\]', raw_report, re.DOTALL)
                        if selected_img_match:
                            selected_img_url = selected_img_match.group(1).strip()
                            
                        # 레포트에서 태그 싹 정리
                        clean_report = re.sub(r'\[BEST_COPY\].*?\[/BEST_COPY\]', '', raw_report, flags=re.DOTALL)
                        clean_report = re.sub(r'\[SELECTED_IMAGE_URL\].*?\[/SELECTED_IMAGE_URL\]', '', clean_report, flags=re.DOTALL).strip()
                        
                        # 🔥 AI가 선별한 이미지에 카피 합성 (로고 원천 차단!)
                        if selected_img_url:
                            status_container.info("🎨 AI가 선별한 상품 이미지에 추천 카피 합성 중...")
                            ad_img = create_ad_image(selected_img_url, best_copy_text)

                    wc_img = None
                    if len(review_txt) >= 50:
                        wc_img = create_wordcloud_summary(review_txt)
                    
                    # 🔥 요청하신 시간/분 형식 완벽 반영: yyyy-mm-dd(화) hh:mm
                    now = datetime.datetime.now()
                    weekdays = ['월', '화', '수', '목', '금', '토', '일']
                    formatted_date = f"{now.strftime('%Y-%m-%d')}({weekdays[now.weekday()]}) {now.strftime('%H:%M')}"
                    now_str = now.strftime("%Y%m%d_%H%M")
                    
                    parsed = urlparse(main_url_input)
                    qs = parse_qs(parsed.query)
                    p_code = qs.get('branduid', qs.get('product_no', ['UNKNOWN']))[0]
                    
                    save_to_google_sheet([formatted_date, p_code, main_url_input, clean_report], worker_input)
                    
                    st.session_state.final_report = clean_report
                    st.session_state.wc_img = wc_img
                    st.session_state.ad_img = ad_img
                    st.session_state.filename_base = f"USP_{p_code}_{now_str}"
                    st.session_state.main_url = main_url_input
                    st.session_state.worker_name = worker_input
                    st.session_state.content_type = content_type_input 
                    st.session_state.analyzed = True
                    st.toast("✅ 맞춤형 분석 및 기획 완료!", icon="🎉")

        if st.session_state.analyzed:
            st.markdown("---")
            result_expander = st.expander("📝 1. AI 맞춤형 기획안 & 카피 (클릭하여 열기)", expanded=True)
            with result_expander:
                st.markdown(st.session_state.final_report)
                st.text_area("📋 결과 복사하기", st.session_state.final_report, height=400)

            if "이미지" in st.session_state.content_type:
                ad_expander = st.expander("🖼️ 2. 추천 광고 소재 시안 (실제 상품 이미지 합성)", expanded=True)
                with ad_expander:
                    if st.session_state.ad_img:
                        st.image(st.session_state.ad_img, caption="AI 추천 카피 자동 합성본")
                        st.download_button("💾 광고 시안(.png) 다운로드", data=st.session_state.ad_img, file_name=f"AD_{st.session_state.filename_base}.png", mime="image/png")
                    else:
                        st.warning("상세페이지에서 적합한 메인 이미지를 선별하지 못해 시안 합성이 생략되었습니다.")

            wordcloud_expander = st.expander("☁️ 3. 리뷰 키워드 워드클라우드", expanded=True)
            with wordcloud_expander:
                if st.session_state.wc_img:
                    st.image(st.session_state.wc_img, caption="리뷰 핵심 키워드")
                    st.download_button("💾 워드클라우드(.png) 다운로드", data=st.session_state.wc_img, file_name=f"WC_{st.session_state.filename_base}.png", mime="image/png")
                else:
                    st.markdown("수집된 리뷰가 없어 워드클라우드를 제공하지 않습니다.")
            
            st.download_button(
                label="💾 전체 기획안(.txt) 일괄 다운로드",
                data=f"분석 대상: {st.session_state.main_url}\n기획 타겟: {st.session_state.content_type}\n작업자: {st.session_state.worker_name}\n==========================\n\n{st.session_state.final_report}",
                file_name=f"{st.session_state.filename_base}.txt",
                mime="text/plain",
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

    st.markdown("<br><center>마케팅 자동화 솔루션 | Internal Tool V10.6</center>", unsafe_allow_html=True)
