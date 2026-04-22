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
from PIL import Image, ImageDraw, ImageFont, ImageFilter

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
for key in ['analyzed', 'main_report_text', 'ad_plan_text', 'wc_img', 'ad_img', 'filename_base', 'main_url', 'worker_name', 'content_type', 'copy_style', 'user_ref_copy', 'extracted_img_url']:
    if key not in st.session_state:
        st.session_state[key] = None if 'img' in key else ""

if 'extra_copies' not in st.session_state:
    st.session_state.extra_copies = []
if 'final_compiled_text' not in st.session_state:
    st.session_state.final_compiled_text = ""

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
            pass

# ==========================================
# [데이터 수집 엔진] 
# ==========================================
def get_data_bulldozer(target_url, max_pages=10):
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
                if any(x in src_lower for x in ['logo', 'icon', 'btn', 'button', '.gif', 'blank']): continue
                
                if src.startswith('//'): src = 'https:' + src
                elif src.startswith('/'):
                    parsed_uri = urlparse(target_url)
                    src = '{uri.scheme}://{uri.netloc}'.format(uri=parsed_uri) + src
                    
                if src not in potential_product_imgs: potential_product_imgs.append(src)
            potential_product_imgs = potential_product_imgs[:20]
        except: pass 

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
# [AI 요약 엔진] ✅ 모델명 수정 (v14)
# ==========================================
def analyze_deep_usp_summarized(brand_text, review_text, potential_imgs, content_type, copy_style, product_url, product_name, user_ref_copy):
    status_container.info(f"🧠 (3/3) 제미나이 AI가 핵심 USP를 압축하여 기획안을 작성 중입니다...")
    
    if "명사/동사" in copy_style:
        style_guide = "모든 카피는 20자 이내로, '명사' 혹은 '동사'로 종결하여 이미지로 즉각 각인시킬 것."
        copy_title = "### 🎯 3. 초압축 다각도 후킹 카피 (각 20자 이내, 명사/동사 종결)"
    else:
        style_guide = "모든 카피는 20자 이내로, 타겟 고객이 친근하게 느낄 수 있는 자연스러운 서술형(문장형)으로 자유롭게 작성할 것."
        copy_title = "### 🎯 3. 초압축 다각도 후킹 카피 (각 20자 이내, 자연스러운 자유 형식)"
    
    ref_section = """
    [자사 베스트 카피 레퍼런스]
    - 입는 순간 -5kg, 마법의 슬림핏
    - 물놀이, 운동, 외출 올인원!
    - 남편이랑 아들이 서로 입겠다고 싸워요
    - 남편 주말 패션 구원템 등장!
    - 작년꺼 또 입어요..? 셔링 디테일로 핏이 달라지는
    """
    
    if user_ref_copy.strip():
        ref_section += f"\n[이번 캠페인 맞춤형 레퍼런스 카피 (최우선 반영)]\n{user_ref_copy}\n-> AI는 위 맞춤형 레퍼런스의 '말투, 결, 느낌'을 최우선으로 모방하여 작성할 것."

    base_prompt = f"""
    # Role: 시니어 커머스 전략가
    
    # 분석 가이드라인:
    1. **핵심 위주 축약**: 의미를 훼손하지 않는 선에서 텍스트를 최대한 간결하고 압축적으로 작성하세요.
    2. **긍정/추천 포인트 우선**: 고객들이 반복해서 칭찬하는 '긍정 포인트 및 추천 이유'를 가장 비중 있게 다루어 균형 잡힌 USP를 도출하세요.
    3. **카피 스타일**: 오직 [3번 후킹 카피] 파트만 {style_guide}

    {ref_section}

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

    image_prompt = f"""
    # [매우 중요] 광고 시안 합성을 위한 이미지 선별:
    아래 [Potential Product Images] 리스트에서 실제 제품/모델이 가장 잘 드러난 사진 URL을 골라주세요.
    [SELECTED_IMAGE_URL]이미지주소[/SELECTED_IMAGE_URL]
    
    # [광고 소재 기획안 표]
    반드시 아래 태그 [AD_PLAN_START] 와 [AD_PLAN_END] 사이에 마크다운 표 형태로만 기획안을 출력하세요.
    [AD_PLAN_START]
    | 구분 | 내용 |
    |---|---|
    | **광고 지면** | GFA 피드, 메인, 카카오 모먼트 등 |
    | **광고 텍스트** | (3번 항목에서 도출한 가장 좋은 메인/서브 카피 2줄) |
    | **버튼(CTA)** | {product_name} > |
    | **URL** | {product_url} |
    | **제품명** | {product_name} |
    | **제작 설명** | - (레퍼런스 이미지 기반 배경 합성 및 레이아웃 지시사항) |
    [AD_PLAN_END]
    
    [Potential Product Images]
    {json.dumps(potential_imgs)}
    """ if "이미지" in content_type else ""

    video_prompt = """
    ### 🎬 숏폼 영상 기획안 (6~15초)
    * **[Hook (0~3초)]**: (상황 묘사)
    * **[Body (3~10초)]**: (시각적 증명)
    * **[Action (10~15초)]**: (구매 유도)
    """ if "영상" in content_type else ""

    final_prompt = base_prompt + image_prompt + video_prompt

    # ✅ 수정된 모델 목록 (v14)
    fallback_models = ['gemini-2.5-pro', 'gemini-2.5-flash', 'gemini-2.0-flash']
    client = genai.Client(api_key=MY_GEMINI_API_KEY)
    last_error = ""
    
    for model_name in fallback_models:
        for attempt in range(2): 
            try:
                if attempt > 0 or model_name != 'gemini-2.5-pro':
                    status_container.warning(f"⚠️ 메인 서버 혼잡으로 예비 서버({model_name})로 우회 재시도 중...")
                response = client.models.generate_content(model=model_name, contents=final_prompt)
                return response.text
            except Exception as e:
                error_msg = str(e)
                last_error = error_msg
                if "503" in error_msg or "high demand" in error_msg or "429" in error_msg:
                    time.sleep(2)
                    continue
                else:
                    break
    return f"🚨 **분석 실패:** 지속적인 서버 폭주이거나, 확인할 수 없는 에러가 발생했습니다.\n👉 **실제 에러 내용:** `{last_error}`"

# ==========================================
# [추가 카피 생성기] ✅ 모델명 수정 (v14)
# ==========================================
def generate_extra_copies(base_report, user_req, copy_style, user_ref_copy):
    if "명사/동사" in copy_style:
        style_guide = "각 20자 이내, '명사' 혹은 '동사'로 종결되는 임팩트형"
    else:
        style_guide = "각 20자 이내, 자연스럽게 고객에게 말 거는 서술형"

    ref_section = "- 입는 순간 -5kg, 마법의 슬림핏\n- 남편이랑 아들이 서로 입겠다고 싸워요"
    if user_ref_copy.strip():
        ref_section = user_ref_copy

    prompt = f"""
    당신은 대한민국 최고 수준의 시니어 카피라이터입니다.
    다음은 우리 제품의 핵심 USP 분석 결과(메인 기획안)입니다:
    {base_report[:2000]}
    
    위 내용 중 [3번. 초압축 다각도 후킹 카피] 항목의 **압축력, 센스, 트렌디함**을 완벽하게 유지하면서, 
    마케터의 아래 [추가 요청사항]을 반영하여 **완전히 새로운 후킹 카피 8개**를 작성해주세요.
    
    [마케터 추가 요청사항]: "{user_req}"
    [카피 제약 조건]: {style_guide}
    [카피 톤앤매너 레퍼런스]: 아래 카피들의 '결(말투, 센스)'을 강력하게 모방할 것.
    {ref_section}
    
    결과는 1. 2. 3. 번호만 매겨서 깔끔하게 출력해주세요. 절대 부연 설명을 달지 마세요.
    """

    # ✅ 수정된 모델 목록 (v14)
    fallback_models = ['gemini-2.5-pro', 'gemini-2.5-flash', 'gemini-2.0-flash']
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
# [이미지 합성 로직] 
# ==========================================
def create_ad_image(img_source, main_copy, sub_copy, product_url, is_file=False):
    try:
        if is_file:
            img = Image.open(img_source).convert("RGBA")
        else:
            req = urllib.request.Request(img_source, headers={'User-Agent': 'Mozilla/5.0'})
            with urllib.request.urlopen(req) as response:
                img_data = response.read()
            img = Image.open(io.BytesIO(img_data)).convert("RGBA")

        base_width = 1080
        w_percent = (base_width / float(img.size[0]))
        h_size = int((float(img.size[1]) * float(w_percent)))
        img = img.resize((base_width, h_size), Image.Resampling.LANCZOS)

        overlay = Image.new('RGBA', img.size, (0, 0, 0, 0))
        draw = ImageDraw.Draw(overlay)
        
        box_top = int(h_size * 0.40)
        for y in range(box_top, h_size):
            progress = (y - box_top) / (h_size - box_top)
            alpha = int(240 * (progress ** 0.5)) 
            draw.line([(0, y), (base_width, y)], fill=(0, 0, 0, alpha))
        
        img = Image.alpha_composite(img, overlay)
        draw = ImageDraw.Draw(img)

        font_b_path = "NanumGothicBold.ttf"
        font_r_path = "NanumGothic.ttf"
        if not os.path.exists(font_b_path):
            urllib.request.urlretrieve("https://github.com/google/fonts/raw/main/ofl/nanumgothic/NanumGothic-Bold.ttf", font_b_path)
        if not os.path.exists(font_r_path):
            urllib.request.urlretrieve("https://github.com/google/fonts/raw/main/ofl/nanumgothic/NanumGothic-Regular.ttf", font_r_path)
            
        font_main = ImageFont.truetype(font_b_path, 80) 
        font_sub = ImageFont.truetype(font_r_path, 50)
        font_logo = ImageFont.truetype(font_b_path, 35)
        
        draw.text((52, 52), "X E X Y M I X", font=font_logo, fill=(0, 0, 0, 150))
        draw.text((50, 50), "X E X Y M I X", font=font_logo, fill=(255, 255, 255, 255))

        def draw_centered_text(text, font, y_pos, color, shadow=True):
            bbox = draw.textbbox((0, 0), text, font=font)
            text_x = (base_width - (bbox[2] - bbox[0])) / 2
            if shadow:
                draw.text((text_x+3, y_pos+3), text, font=font, fill=(0,0,0,150))
            draw.text((text_x, y_pos), text, font=font, fill=color)

        start_y = int(h_size * 0.65)
        
        sub_copy = sub_copy if sub_copy else "시선을 사로잡는 디테일"
        draw_centered_text(sub_copy, font_sub, start_y, (230, 230, 230, 255))
        
        main_copy = main_copy if main_copy else "매력적인 메인 카피"
        draw_centered_text(main_copy, font_main, start_y + 80, (255, 255, 255, 255))
        
        img_buffer = io.BytesIO()
        img.convert("RGB").save(img_buffer, format="PNG")
        return img_buffer.getvalue()
    except Exception as e: 
        return None

# ==========================================
# [워드클라우드 로직] ✅ 모델명 수정 (v14)
# ==========================================
def create_wordcloud_summary(review_text):
    try:
        time.sleep(1) 
        wc_prompt = f"다음 대량의 리뷰에서 가장 많이 언급된 제품 장점 및 추천 키워드(명사형) 100개만 추출해서 콤마(,)로만 구분해서 출력해.\n{review_text[:8000]}"
        client = genai.Client(api_key=MY_GEMINI_API_KEY)
        
        # ✅ 수정된 모델 목록 (v14)
        fallback_models = ['gemini-2.5-pro', 'gemini-2.5-flash', 'gemini-2.0-flash']
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
        
        wordcloud = WordCloud(
            font_path=font_path, width=600, height=400, 
            background_color='white', colormap='tab10', 
            max_words=100, prefer_horizontal=0.9, margin=2
        ).generate(keywords)
        
        img_buffer = io.BytesIO()
        plt.figure(figsize=(6, 4)) 
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
            
            content_type_input = st.selectbox("🎬 기획안 타겟 선택", ["이미지+영상", "이미지", "영상", "USP만 추출 (기획안 제외)"], index=3)
            copy_style_input = st.selectbox("✍️ 카피라이팅 스타일", ["명사/동사 중심 (임팩트형)", "자유 형식 (자연스러운 서술형)"], index=1)
            user_ref_input = st.text_area("📝 캠페인 레퍼런스 카피 (선택사항)", placeholder="최근 터진 카피나 비슷한 무드의 카피를 넣어주시면 AI가 그 '결'을 모방합니다.\n(예: 입는 순간 -5kg 마법의 슬림핏)", height=100)
            st.markdown("---")
            
            main_url_input = st.text_input("🔗 분석할 상품 URL", value="", placeholder="URL을 입력하세요")
            st.caption("✔️ 작성 예시: https://www.xexymix.com/shop/shopdetail.html?branduid=2077700")
            max_pages_input = st.slider("📜 수집 페이지 수", 10, 50, 10, 5) 
        
        col_btn1, col_btn2, col_btn3 = st.columns([1, 1, 1])
        with col_btn2:
            start_btn = st.button("▶ 분석 시작 및 시트 저장", type="primary", use_container_width=True)

        status_container = st.container()

        if start_btn:
            if not worker_input or not main_url_input:
                st.warning("⚠️ 이름과 URL을 모두 입력해주세요!")
            else:
                with status_container:
                    st.session_state.extra_copies = []
                    st.session_state.ad_img = None
                    st.session_state.final_compiled_text = ""
                    
                    brand_txt, review_txt, potential_imgs, product_name = get_data_bulldozer(main_url_input, max_pages_input)
                    raw_report = analyze_deep_usp_summarized(brand_txt, review_txt, potential_imgs, content_type_input, copy_style_input, main_url_input, product_name, user_ref_input)
                    
                    if "🚨" in raw_report:
                        st.session_state.main_report_text = raw_report
                        st.session_state.analyzed = True
                    else:
                        ad_plan_match = re.search(r'\[AD_PLAN_START\](.*?)\[AD_PLAN_END\]', raw_report, re.DOTALL)
                        if ad_plan_match:
                            st.session_state.ad_plan_text = ad_plan_match.group(1).strip()
                            clean_report = raw_report.replace(ad_plan_match.group(0), "").replace("[AD_PLAN_START]", "").replace("[AD_PLAN_END]", "").strip()
                        else:
                            st.session_state.ad_plan_text = "기획안을 추출하지 못했습니다. (USP만 추출 모드)"
                            clean_report = raw_report.strip()

                        selected_img_match = re.search(r'\[SELECTED_IMAGE_URL\](.*?)\[/SELECTED_IMAGE_URL\]', clean_report, re.DOTALL)
                        if selected_img_match:
                            st.session_state.extracted_img_url = selected_img_match.group(1).strip()
                            clean_report = re.sub(r'\[SELECTED_IMAGE_URL\].*?\[/SELECTED_IMAGE_URL\]', '', clean_report, flags=re.DOTALL).strip()
                        
                        st.session_state.main_report_text = clean_report
                        st.session_state.wc_img = create_wordcloud_summary(review_txt) if len(review_txt) >= 50 else None
                        
                        # ✅ formatted_date 버그 수정 (v14)
                        kst_now = datetime.datetime.utcnow() + datetime.timedelta(hours=9)
                        formatted_date = kst_now.strftime("%Y-%m-%d %H:%M")
                        now_str = kst_now.strftime("%Y%m%d_%H%M")

                        parsed = urlparse(main_url_input)
                        qs = parse_qs(parsed.query)
                        p_code = qs.get('branduid', qs.get('product_no', ['UNKNOWN']))[0]
                        
                        save_to_google_sheet([formatted_date, product_name, p_code, main_url_input, clean_report], worker_input)
                        
                        st.session_state.filename_base = f"USP_{p_code}_{now_str}"
                        st.session_state.main_url = main_url_input
                        st.session_state.worker_name = worker_input
                        st.session_state.content_type = content_type_input 
                        st.session_state.copy_style = copy_style_input
                        st.session_state.user_ref_copy = user_ref_input
                        st.session_state.analyzed = True
                        st.toast("✅ 맞춤형 분석 완료!", icon="🎉")

        if st.session_state.analyzed:
            st.markdown("---")
            st.markdown("### 📝 1. 핵심 USP & 후킹 카피 (초안)")
            st.markdown(st.session_state.main_report_text)

            if "🚨" not in st.session_state.main_report_text:
                st.markdown("<br>", unsafe_allow_html=True)
                st.markdown("### 💡 2. 카피라이팅 추가 추출기")
                st.info("메인 분석 결과를 바탕으로, 마케터의 의도를 담은 카피를 무제한으로 추가 적재할 수 있습니다.")
                
                col_extra1, col_extra2 = st.columns([4, 1])
                with col_extra1:
                    extra_req = st.text_input("👇 원하는 소구점이나 무드를 적어주세요.", placeholder="예: 봄 시즌에 맞춰서 화사하게, 40대 타겟으로 변경해서 등")
                with col_extra2:
                    st.write("") 
                    if st.button("➕ 카피 8개 추가", use_container_width=True):
                        if extra_req:
                            with st.spinner("마케터님의 의도를 반영하여 새로운 카피를 뽑는 중..."):
                                new_copies = generate_extra_copies(st.session_state.main_report_text, extra_req, st.session_state.copy_style, st.session_state.user_ref_copy)
                                st.session_state.extra_copies.append({"req": extra_req, "result": new_copies})
                        else:
                            st.warning("요청사항을 입력해주세요!")
                
                for idx, extra in enumerate(st.session_state.extra_copies):
                    with st.expander(f"💬 추가 추출 #{idx+1} (요청: {extra['req']})", expanded=True):
                        st.markdown(extra['result'])
                
                st.markdown("<br>", unsafe_allow_html=True)
                st.markdown("### 📋 3. 광고 소재 기획안 수정")
                if "이미지" in st.session_state.content_type:
                    st.session_state.ad_plan_text = st.text_area("아래 기획안 초안을 자유롭게 수정하세요.", value=st.session_state.ad_plan_text, height=200)
                else:
                    st.info("기획안 타겟이 'USP만 추출'로 설정되어 기획안 생성이 생략되었습니다.")

                if "이미지" in st.session_state.content_type:
                    st.markdown("<br>", unsafe_allow_html=True)
                    st.markdown("### 🖼️ 4. 광고 시안 제작 (선택)")
                    
                    sim_col1, sim_col2 = st.columns(2)
                    with sim_col1:
                        sim_mode = st.radio("시안 제작 방식", ["🤖 AI 추출 이미지 사용", "📁 직접 이미지 업로드"])
                        sim_main = st.text_input("메인 카피 (위 기획안에서 복사해오세요)", "시선을 끄는 메인 카피")
                        sim_sub = st.text_input("서브 카피 (위 기획안에서 복사해오세요)", "자연스럽게 시선을 사로잡는")
                        
                        uploaded_file = None
                        if sim_mode == "📁 직접 이미지 업로드":
                            uploaded_file = st.file_uploader("상품 이미지를 업로드하세요 (JPG, PNG)", type=["jpg", "jpeg", "png"])
                        
                        if st.button("🖼️ 위 내용으로 시안 생성하기", type="secondary"):
                            with st.spinner("이미지 합성 중..."):
                                if sim_mode == "📁 직접 이미지 업로드" and uploaded_file:
                                    st.session_state.ad_img = create_ad_image(uploaded_file, sim_main, sim_sub, st.session_state.main_url, is_file=True)
                                elif sim_mode == "🤖 AI 추출 이미지 사용" and st.session_state.extracted_img_url:
                                    st.session_state.ad_img = create_ad_image(st.session_state.extracted_img_url, sim_main, sim_sub, st.session_state.main_url, is_file=False)
                                else:
                                    st.warning("이미지 소스를 찾을 수 없습니다. (직접 업로드 또는 AI URL 확인)")
                                    
                    with sim_col2:
                        st.write("결과 미리보기 (1/4 축소 사이즈)")
                        if st.session_state.ad_img:
                            _, img_col, _ = st.columns([1, 2, 1])
                            with img_col:
                                st.image(st.session_state.ad_img, use_container_width=True)
                            st.download_button("💾 시안 다운로드", data=st.session_state.ad_img, file_name=f"AD_{st.session_state.filename_base}.png", mime="image/png")
                        else:
                            st.info("좌측에서 시안 생성 버튼을 눌러주세요.")

                st.markdown("<br>", unsafe_allow_html=True)
                st.markdown("### ✅ 5. 최종 결과물 취합 및 복사")
                
                if st.session_state.wc_img:
                     with st.expander("☁️ (참고용) 리뷰 키워드 워드클라우드 확인", expanded=False):
                         st.image(st.session_state.wc_img, caption="리뷰 핵심 키워드")
                         st.download_button("💾 워드클라우드 다운로드", data=st.session_state.wc_img, file_name=f"WC_{st.session_state.filename_base}.png", mime="image/png")

                if st.button("🚀 지금까지 작업한 모든 내용 합치기", type="primary", use_container_width=True):
                    final_text = f"분석 대상: {st.session_state.main_url}\n기획 타겟: {st.session_state.content_type}\n==========================\n\n{st.session_state.main_report_text}\n\n"
                    
                    if st.session_state.extra_copies:
                        final_text += "==========================\n[추가 카피 적재 내역]\n"
                        for idx, extra in enumerate(st.session_state.extra_copies):
                            final_text += f"\n▶ 추가 #{idx+1} (요청: {extra['req']})\n{extra['result']}\n"
                    
                    if "이미지" in st.session_state.content_type:
                        final_text += f"\n==========================\n[최종 수정 기획안]\n{st.session_state.ad_plan_text}\n"
                        
                    st.session_state.final_compiled_text = final_text

                if st.session_state.final_compiled_text:
                    st.text_area("📋 [전체 선택 (Ctrl+A) 후 복사하세요]", value=st.session_state.final_compiled_text, height=300)

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
                except:
                    st.warning(f"💡 [{selected_sheet}] 탭은 비어있거나 첫 줄(제목 행)이 없어서 표를 만들 수 없습니다.")

    st.markdown("<br><center>마케팅 자동화 솔루션 | Internal Tool V14.0 (Gemini Model Fixed)</center>", unsafe_allow_html=True)
