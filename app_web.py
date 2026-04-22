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
import matplotlib
matplotlib.use('Agg')
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
st.set_page_config(page_title="AI USP 추출 솔루션", page_icon=":dart:", layout="wide")

try:
    APP_PASSWORD = st.secrets["APP_PASSWORD"] 
    MY_GEMINI_API_KEY = st.secrets["GEMINI_API_KEY"]
except:
    APP_PASSWORD = "123"
    MY_GEMINI_API_KEY = "임시"

GOOGLE_SHEET_NAME = "USP_추출기" 

if 'authenticated' not in st.session_state:
    st.session_state.authenticated = False
for key in ['analyzed', 'final_report', 'wc_img', 'ad_img', 'filename_base', 'main_url', 'worker_name', 'content_type', 'copy_style']:
    if key not in st.session_state:
        st.session_state[key] = None if 'img' in key else ""

# 🔥 카피 무한 적재를 위한 임시 창고 생성
if 'extra_copies' not in st.session_state:
    st.session_state.extra_copies = []

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
                worksheet.append_row(["날짜", "상품명", "상품코드", "URL", "분석결과"])
            worksheet.append_row(data_list)
        except Exception as e:
            st.error(f"🚨 시트 기록 실패: {e}")

# ==========================================
# [데이터 수집 엔진] 
# ==========================================
def get_data_bulldozer(target_url, max_pages=30):
    brand_text = ""
    review_list = []
    potential_product_imgs = [] 
    product_name = "상품명 수집 불가" 
    
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
        try:
            res = requests.get(target_url, headers={'User-Agent': 'Mozilla/5.0'}, timeout=10)
            soup = BeautifulSoup(res.text, 'html.parser')
            
            og_title = soup.find('meta', property='og:title')
            if og_title and og_title.get('content'):
                product_name = og_title['content'].strip()
            else:
                title_tag = soup.find('title')
                if title_tag:
                    product_name = title_tag.text.strip()
            
            og_img = soup.find('meta', property='og:image')
            if og_img and og_img.get('content'):
                og_src = og_img['content']
                if 'logo' not in og_src.lower(): 
                    potential_product_imgs.append(og_src)
                    
            for img in soup.find_all('img'):
                src = img.get('src', '') or img.get('data-src', '')
                if not src: continue
                
                src_lower = src.lower()
                if any(x in src_lower for x in ['logo', 'icon', 'btn', 'button', '.gif', 'blank']):
                    continue
                    
                if src.startswith('//'):
                    src = 'https:' + src
                elif src.startswith('/'):
                    parsed_uri = urlparse(target_url)
                    src = '{uri.scheme}://{uri.netloc}'.format(uri=parsed_uri) + src
                    
                if src not in potential_product_imgs:
                    potential_product_imgs.append(src)
            
            potential_product_imgs = potential_product_imgs[:20]
                
        except Exception as e:
            pass 

        service = Service("/usr/bin/chromedriver") 
        driver = webdriver.Chrome(service=service, options=options)
        driver.set_page_load_timeout(15)
        
        status_container.info(f"🚀 (1/3) 상세페이지 텍스트 및 상품명 수집 중...")
        try:
            driver.get(target_url)
            time.sleep(2)
            if product_name == "상품명 수집 불가" or not product_name:
                product_name = driver.title
        except:
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
        
    return brand_text, "\n".join(review_list)[:30000], potential_product_imgs, product_name 

# ==========================================
# [AI 요약 엔진] 🔥 긍정/추천 중심 및 길이 축약 로직 반영
# ==========================================
def analyze_deep_usp_summarized(brand_text, review_text, potential_imgs, content_type, copy_style, product_url, product_name):
    status_container.info(f"🧠 (3/3) 제미나이 AI가 핵심 USP를 압축하여 기획안을 작성 중입니다...")
    
    if "명사/동사" in copy_style:
        style_guide = "모든 카피는 20자 이내로, '명사' 혹은 '동사'로 종결하여 이미지로 즉각 각인시킬 것."
        copy_title = "### 🎯 3. 초압축 다각도 후킹 카피 (각 20자 이내, 명사/동사 종결)"
    else:
        style_guide = "모든 카피는 20자 이내로, 타겟 고객이 친근하게 느낄 수 있는 자연스러운 서술형(문장형)으로 자유롭게 작성할 것."
        copy_title = "### 🎯 3. 초압축 다각도 후킹 카피 (각 20자 이내, 자연스러운 자유 형식)"
    
    base_prompt = f"""
    # Role: 시니어 커머스 전략가
    
    # 분석 가이드라인 (매우 중요):
    1. **핵심 위주 축약**: 바쁜 실무자가 바로 읽고 쓸 수 있도록, 의미를 훼손하지 않는 선에서 텍스트를 최대한 간결하고 압축적으로 작성하세요.
    2. **긍정/추천 포인트 우선**: 고객들이 반복해서 칭찬하는 '긍정 포인트 및 추천 이유'를 가장 비중 있게 다루어 균형 잡힌 USP를 도출하세요. 불만 해결 요소도 중요하지만 긍정 리뷰가 메인입니다.
    3. **카피 스타일**: 오직 [3번 후킹 카피] 파트만 {style_guide}
    4. 오직 최종 결과 텍스트만 깔끔하게 마크다운으로 출력하세요.

    ---
    # Input Data:
    [상세페이지 텍스트]
    {brand_text}
    
    [고객 리뷰 데이터 전량]
    {review_text if len(review_text) > 50 else "현재 수집된 리뷰 데이터가 없습니다."}
    ---

    ### 🏢 1. 핵심 소구점 요약 (상세페이지 기반)
    1. **[디자인/핏]**: (간략히)
    2. **[기능/소재]**: (간략히)
    3. **[활용 상황]**: (간략히)

    ### 🗣️ 2. 고객의 '진짜 긍정' 리뷰 (추천 이유 중심)
    * **[반복되는 극찬 포인트 Top 3]**: 
    * **[해결된 불편함 (Pain-point 극복)]**: 
    * **[고객의 한 마디]**: 

    {copy_title}
    1. **[추천/만족형]** 2. **[시간 단축형]** 3. **[시각 보정형]** 4. **[피부/소재 공감형]** 5. **[가성비 증명형]** 6. **[상황 저격형]** 7. **[사회적 증거형]** 8. **[불만 해결형]** """

    image_prompt = ""
    if "이미지" in content_type:
        image_prompt = f"""
    ### 💡 4. 소재 제작 기획안 (배너 이미지용)
    *현업 마케팅 팀의 표준 양식에 맞추어 아래 표(Table) 형태로 기획안을 작성하세요.*
    
    # [매우 중요] 광고 시안 합성을 위한 카피 및 이미지 선별:
    아래 [Potential Product Images] 리스트에서 실제 제품이나 모델이 가장 잘 드러난 사진 URL을 딱 1개만 골라주세요. (로고만 있으면 None)
    
    [SELECTED_IMAGE_URL]이미지주소[/SELECTED_IMAGE_URL]
    [MAIN_COPY]시선을 끄는 메인 카피 (15자 이내)[/MAIN_COPY]
    [SUB_COPY]받쳐주는 서브 카피 (25자 이내)[/SUB_COPY]
    
    #### 📋 광고 소재 제작 지시서
    | 구분 | 내용 |
    |---|---|
    | **광고 지면** | GFA 피드, 메인, 카카오 모먼트 등 |
    | **광고 텍스트** | (위에서 작성한 메인/서브 카피를 자연스럽게 2줄로 기재) |
    | **버튼(CTA)** | {product_name} > |
    | **URL** | {product_url} |
    | **제품명** | {product_name} |
    | **제작 설명** | - (레퍼런스 이미지 기반 배경 합성 및 레이아웃 지시사항 간략 기재) |
    | **레퍼런스** | (위에서 선별한 SELECTED_IMAGE_URL 을 그대로 기재) |
    
    [Potential Product Images]
    {json.dumps(potential_imgs)}
    """

    video_prompt = ""
    if "영상" in content_type:
        video_num = "5" if "이미지" in content_type else "4"
        video_prompt = f"""
    ### 🎬 {video_num}. 숏폼 영상 기획안 (6~15초)
    * **[Hook (0~3초)]**: (상황 묘사)
    * **[Body (3~10초)]**: (시각적 증명)
    * **[Action (10~15초)]**: (구매 유도)
    """

    final_prompt = base_prompt + image_prompt + video_prompt

    # 🔥 2.5-flash 우선 시도 후 1.5-flash 우회 (Dual-Engine)
    fallback_models = ['gemini-2.5-flash', 'gemini-1.5-flash']
    client = genai.Client(api_key=MY_GEMINI_API_KEY)
    
    for model_name in fallback_models:
        for attempt in range(2): 
            try:
                if attempt > 0 or model_name == 'gemini-1.5-flash':
                    status_container.warning(f"⚠️ 메인 서버 혼잡으로 예비 서버({model_name})로 우회 재시도 중...")
                response = client.models.generate_content(model=model_name, contents=final_prompt)
                return response.text
            except Exception as e:
                error_msg = str(e)
                if "503" in error_msg or "high demand" in error_msg:
                    time.sleep(2)
                    continue
                else:
                    break 
    return "🚨 **모든 AI 서버가 현재 폭주 상태입니다.** 서버 트래픽이 안정된 후 다시 시도해주세요."

# ==========================================
# [추가 카피 무한 생성기] 🔥 신규 기능
# ==========================================
def generate_extra_copies(base_report, user_req, copy_style):
    if "명사/동사" in copy_style:
        style_guide = "각 20자 이내, '명사' 혹은 '동사'로 종결되는 임팩트형"
    else:
        style_guide = "각 20자 이내, 자연스럽게 고객에게 말 거는 서술형"

    prompt = f"""
    다음은 우리 제품의 핵심 USP 분석 결과입니다:
    {base_report}
    
    이 분석 결과를 바탕으로, 마케터의 아래 [추가 요청사항]을 완벽하게 반영하여 **완전히 새로운 후킹 카피 8개**를 추가로 작성해주세요.
    
    [마케터 추가 요청사항]: "{user_req}"
    [카피 제약 조건]: {style_guide}
    
    결과는 1번부터 8번까지 번호를 매겨서 깔끔하게 출력해주세요. 다른 설명은 생략하세요.
    """
    fallback_models = ['gemini-2.5-flash', 'gemini-1.5-flash']
    client = genai.Client(api_key=MY_GEMINI_API_KEY)
    for model_name in fallback_models:
        for attempt in range(2):
            try:
                response = client.models.generate_content(model=model_name, contents=prompt)
                return response.text
            except:
                time.sleep(1)
                continue
    return "🚨 서버 지연으로 추가 카피 생성에 실패했습니다. 다시 시도해주세요."

# ==========================================
# [이미지 합성 로직] 🔥 퀄리티 대폭 상승 (그라데이션 & 로고 추가)
# ==========================================
def create_ad_image(img_url, main_copy, sub_copy, product_url):
    if not img_url or img_url == "None": 
        return None
    try:
        req = urllib.request.Request(img_url, headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req) as response:
            img_data = response.read()
        img = Image.open(io.BytesIO(img_data)).convert("RGBA")

        base_width = 1080
        w_percent = (base_width / float(img.size[0]))
        h_size = int((float(img.size[1]) * float(w_percent)))
        img = img.resize((base_width, h_size), Image.Resampling.LANCZOS)

        overlay = Image.new('RGBA', img.size, (0, 0, 0, 0))
        draw = ImageDraw.Draw(overlay)
        
        # 🔥 딱딱한 박스 대신 부드러운 하단 투명 그라데이션 적용
        box_top = int(h_size * 0.45)
        for y in range(box_top, h_size):
            progress = (y - box_top) / (h_size - box_top)
            alpha = int(220 * progress) # 점진적으로 어두워짐 (Max 220)
            draw.line([(0, y), (base_width, y)], fill=(0, 0, 0, alpha))
        
        img = Image.alpha_composite(img, overlay)
        draw = ImageDraw.Draw(img)

        font_b_path = "NanumGothicBold.ttf"
        font_r_path = "NanumGothic.ttf"
        if not os.path.exists(font_b_path):
            urllib.request.urlretrieve("https://github.com/google/fonts/raw/main/ofl/nanumgothic/NanumGothic-Bold.ttf", font_b_path)
        if not os.path.exists(font_r_path):
            urllib.request.urlretrieve("https://github.com/google/fonts/raw/main/ofl/nanumgothic/NanumGothic-Regular.ttf", font_r_path)
            
        font_main = ImageFont.truetype(font_b_path, 75) # 폰트 크기 업그레이드
        font_sub = ImageFont.truetype(font_r_path, 45)
        font_logo = ImageFont.truetype(font_b_path, 40)
        
        # 🔥 좌측 상단에 고급스러운 텍스트 로고 배치
        draw.text((50, 50), "X E X Y M I X", font=font_logo, fill=(255, 255, 255, 230))

        def draw_centered_text(text, font, y_pos, color):
            bbox = draw.textbbox((0, 0), text, font=font)
            text_x = (base_width - (bbox[2] - bbox[0])) / 2
            draw.text((text_x, y_pos), text, font=font, fill=color)

        start_y = int(h_size * 0.7)
        
        # 서브 카피가 메인 카피 위로 올라가는 세련된 레이아웃
        sub_copy = sub_copy if sub_copy else "시선을 사로잡는 디테일"
        draw_centered_text(sub_copy, font_sub, start_y - 60, (230, 230, 230, 255))
        
        main_copy = main_copy if main_copy else "매력적인 메인 카피"
        draw_centered_text(main_copy, font_main, start_y + 10, (255, 255, 255, 255))
        
        img_buffer = io.BytesIO()
        img.convert("RGB").save(img_buffer, format="PNG")
        return img_buffer.getvalue()
    except Exception as e: 
        return None

# ==========================================
# [워드클라우드 로직] 🔥 다채로운 컬러 & 크기 2/3 축소
# ==========================================
def create_wordcloud_summary(review_text):
    try:
        time.sleep(1) 
        wc_prompt = f"다음 대량의 리뷰에서 가장 많이 언급된 제품 장점 및 추천 키워드(명사형) 100개만 추출해서 콤마(,)로만 구분해서 출력해.\n{review_text[:8000]}"
        client = genai.Client(api_key=MY_GEMINI_API_KEY)
        
        fallback_models = ['gemini-2.5-flash', 'gemini-1.5-flash']
        keywords = ""
        for model_name in fallback_models:
            for attempt in range(2):
                 try:
                     keywords = client.models.generate_content(model=model_name, contents=wc_prompt).text
                     break 
                 except:
                     time.sleep(1) 
                     continue
            if keywords: break 
            
        if not keywords: return None
        
        font_path = "NanumGothic.ttf"
        if not os.path.exists(font_path):
            urllib.request.urlretrieve("https://github.com/google/fonts/raw/main/ofl/nanumgothic/NanumGothic-Regular.ttf", font_path)
        
        # 🔥 크기 축소 (600x400) 및 다채로운 컬러맵(tab10) 적용, 여백 최소화
        wordcloud = WordCloud(
            font_path=font_path, width=600, height=400, 
            background_color='white', colormap='tab10', 
            max_words=100, prefer_horizontal=0.9, margin=2
        ).generate(keywords)
        
        img_buffer = io.BytesIO()
        plt.figure(figsize=(6, 4)) # 피규어 사이즈 2/3로 축소
        plt.imshow(wordcloud, interpolation='bilinear')
        plt.axis('off')
        plt.tight_layout(pad=0)
        plt.savefig(img_buffer, format='png', bbox_inches='tight', dpi=150)
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
            
            # 🔥 기본값(디폴트) 설정 완료
            content_type_input = st.selectbox("🎬 기획안 타겟 선택", ["이미지+영상", "이미지", "영상", "USP만 추출 (기획안 제외)"], index=3)
            copy_style_input = st.selectbox("✍️ 카피라이팅 스타일", ["명사/동사 중심 (임팩트형)", "자유 형식 (자연스러운 서술형)"], index=1)
            st.markdown("---")
            
            # 🔥 URL 입력란 하단에 친절한 예시 문구 추가
            main_url_input = st.text_input("🔗 분석할 상품 URL", value="", placeholder="URL을 입력하세요")
            st.caption("✔️ 작성 예시: https://www.xexymix.com/shop/shopdetail.html?branduid=2077700")
            
            max_pages_input = st.slider("📜 수집 페이지 수", 10, 50, 10, 5) # 디폴트 10으로 변경
        
        col_btn1, col_btn2, col_btn3 = st.columns([1, 1, 1])
        with col_btn2:
            start_btn = st.button("▶ 분석 시작 및 시트 저장", type="primary", use_container_width=True)

        status_container = st.container()

        if start_btn:
            if not worker_input or not main_url_input:
                st.warning("⚠️ 이름과 URL을 모두 입력해주세요!")
            else:
                with status_container:
                    # 새로운 분석을 시작하면 기존 추가 카피 창고 초기화
                    st.session_state.extra_copies = []
                    
                    brand_txt, review_txt, potential_imgs, product_name = get_data_bulldozer(main_url_input, max_pages_input)
                    raw_report = analyze_deep_usp_summarized(brand_txt, review_txt, potential_imgs, content_type_input, copy_style_input, main_url_input, product_name)
                    
                    if "🚨" in raw_report:
                        st.session_state.final_report = raw_report
                        st.session_state.analyzed = True
                    else:
                        main_copy_text = ""
                        sub_copy_text = ""
                        selected_img_url = None
                        clean_report = raw_report
                        ad_img = None
                        
                        if "이미지" in content_type_input:
                            main_copy_match = re.search(r'\[MAIN_COPY\](.*?)\[/MAIN_COPY\]', raw_report, re.DOTALL)
                            sub_copy_match = re.search(r'\[SUB_COPY\](.*?)\[/SUB_COPY\]', raw_report, re.DOTALL)
                            
                            if main_copy_match: main_copy_text = main_copy_match.group(1).strip()
                            if sub_copy_match: sub_copy_text = sub_copy_match.group(1).strip()
                            
                            selected_img_match = re.search(r'\[SELECTED_IMAGE_URL\](.*?)\[/SELECTED_IMAGE_URL\]', raw_report, re.DOTALL)
                            if selected_img_match:
                                selected_img_url = selected_img_match.group(1).strip()
                                
                            clean_report = re.sub(r'\[MAIN_COPY\].*?\[/MAIN_COPY\]', '', raw_report, flags=re.DOTALL)
                            clean_report = re.sub(r'\[SUB_COPY\].*?\[/SUB_COPY\]', '', clean_report, flags=re.DOTALL)
                            clean_report = re.sub(r'\[SELECTED_IMAGE_URL\].*?\[/SELECTED_IMAGE_URL\]', '', clean_report, flags=re.DOTALL).strip()
                            
                            if selected_img_url:
                                status_container.info("🎨 AI가 선별한 상품 이미지에 추천 카피 합성 중...")
                                ad_img = create_ad_image(selected_img_url, main_copy_text, sub_copy_text, main_url_input)

                        wc_img = None
                        if len(review_txt) >= 50:
                            wc_img = create_wordcloud_summary(review_txt)
                        
                        kst_now = datetime.datetime.utcnow() + datetime.timedelta(hours=9)
                        weekdays = ['월', '화', '수', '목', '금', '토', '일']
                        formatted_date = f"{kst_now.strftime('%Y-%m-%d')}({weekdays[kst_now.weekday()]}) {kst_now.strftime('%H:%M')}"
                        now_str = kst_now.strftime("%Y%m%d_%H%M")
                        
                        parsed = urlparse(main_url_input)
                        qs = parse_qs(parsed.query)
                        p_code = qs.get('branduid', qs.get('product_no', ['UNKNOWN']))[0]
                        
                        save_to_google_sheet([formatted_date, product_name, p_code, main_url_input, clean_report], worker_input)
                        
                        st.session_state.final_report = clean_report
                        st.session_state.wc_img = wc_img
                        st.session_state.ad_img = ad_img
                        st.session_state.filename_base = f"USP_{p_code}_{now_str}"
                        st.session_state.main_url = main_url_input
                        st.session_state.worker_name = worker_input
                        st.session_state.content_type = content_type_input 
                        st.session_state.copy_style = copy_style_input
                        st.session_state.analyzed = True
                        st.toast("✅ 맞춤형 분석 및 기획 완료!", icon="🎉")

        # 분석 결과 노출
        if st.session_state.analyzed:
            st.markdown("---")
            result_expander = st.expander("📝 1. AI 맞춤형 기획안 & 카피 (클릭하여 열기)", expanded=True)
            with result_expander:
                st.markdown(st.session_state.final_report)
                st.text_area("📋 결과 복사하기", st.session_state.final_report, height=300)

            # 🔥 2. 무한 카피 생성기 (추가 적재) UI
            if "🚨" not in st.session_state.final_report:
                st.markdown("### 💡 카피라이팅 추가 추출기")
                st.info("메인 분석 결과를 바탕으로, 마케터의 의도를 담은 카피를 무제한으로 계속 뽑아보세요!")
                
                col_extra1, col_extra2 = st.columns([4, 1])
                with col_extra1:
                    extra_req = st.text_input("👇 원하는 소구점이나 무드를 자유롭게 적어주세요.", placeholder="예: 봄 시즌에 맞춰서 화사하게, 40대 타겟으로 변경해서, 신축성을 강조해서 등")
                with col_extra2:
                    st.write("") # 버튼 줄맞춤
                    if st.button("➕ 카피 8개 추가", use_container_width=True):
                        if extra_req:
                            with st.spinner("AI가 마케터님의 의도를 반영하여 새로운 카피를 뽑는 중..."):
                                new_copies = generate_extra_copies(st.session_state.final_report, extra_req, st.session_state.copy_style)
                                # 결과 적재 (과거 내역이 위로 쌓이게)
                                st.session_state.extra_copies.insert(0, {"req": extra_req, "result": new_copies})
                        else:
                            st.warning("요청사항을 입력해주세요!")
                
                # 추가된 카피들 누적해서 보여주기
                for idx, extra in enumerate(st.session_state.extra_copies):
                    with st.expander(f"💬 추가 추출 #{len(st.session_state.extra_copies) - idx} (요청: {extra['req']})", expanded=True):
                        st.markdown(extra['result'])
                
                st.markdown("---")

                if "이미지" in st.session_state.content_type:
                    ad_expander = st.expander("🖼️ 3. 추천 광고 소재 시안 (실제 상품 이미지 합성)", expanded=True)
                    with ad_expander:
                        if st.session_state.ad_img:
                            st.image(st.session_state.ad_img, caption=f"AI 텍스트 레이아웃 합성본 ({st.session_state.copy_style})")
                            st.download_button("💾 광고 시안(.png) 다운로드", data=st.session_state.ad_img, file_name=f"AD_{st.session_state.filename_base}.png", mime="image/png")
                        else:
                            st.warning("적합한 상품 이미지를 찾지 못했습니다.")

                wordcloud_expander = st.expander("☁️ 4. 리뷰 키워드 워드클라우드", expanded=True)
                with wordcloud_expander:
                    if st.session_state.wc_img:
                        st.image(st.session_state.wc_img, caption="리뷰 핵심 키워드")
                        st.download_button("💾 워드클라우드(.png) 다운로드", data=st.session_state.wc_img, file_name=f"WC_{st.session_state.filename_base}.png", mime="image/png")
                    else:
                        st.markdown("⚠️ 수집된 리뷰가 너무 적거나, 일시적인 AI 트래픽 과부하로 인해 워드클라우드 생성이 생략되었습니다.")
                
                # 다운로드 통합 텍스트 만들기 (추가 카피 포함)
                download_text = f"분석 대상: {st.session_state.main_url}\n기획 타겟: {st.session_state.content_type}\n카피 스타일: {st.session_state.copy_style}\n작업자: {st.session_state.worker_name}\n==========================\n\n{st.session_state.final_report}\n\n"
                if st.session_state.extra_copies:
                    download_text += "==========================\n[추가 카피 적재 내역]\n"
                    for extra in st.session_state.extra_copies:
                        download_text += f"\n▶ 요청: {extra['req']}\n{extra['result']}\n"

                st.download_button(
                    label="💾 전체 기획안(.txt) 일괄 다운로드 (추가 카피 포함)",
                    data=download_text,
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
                try:
                    data = spreadsheet.worksheet(selected_sheet).get_all_records()
                    if data:
                        st.dataframe(data, use_container_width=True)
                    else:
                        st.info(f"[{selected_sheet}] 탭에 아직 저장된 분석 내역이 없습니다.")
                except Exception as e:
                    st.warning(f"💡 [{selected_sheet}] 탭은 비어있거나 첫 줄(제목 행)이 없어서 표를 만들 수 없습니다. 새로운 분석을 1회 진행하시면 자동으로 채워집니다!")

    st.markdown("<br><center>마케팅 자동화 솔루션 | Internal Tool V12.0 (All-in-One)</center>", unsafe_allow_html=True)
