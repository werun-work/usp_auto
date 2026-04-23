# [버전 정보: V16.4 / 업데이트 일자: 2024-04-24]
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
import pandas as pd

# ==========================================
# [1. 초기 세팅 및 세션 관리]
# ==========================================
st.set_page_config(page_title="AI USP 추출 솔루션", page_icon=":dart:", layout="wide")

st.markdown("""
    <style>
    textarea::placeholder { font-size: 13px !important; }
    .vertical-line { border-left: 2px solid #e6e6e6; height: 100%; min-height: 400px; margin: 0 auto; }
    </style>
""", unsafe_allow_html=True)

try:
    APP_PASSWORD = st.secrets["APP_PASSWORD"] 
    MY_GEMINI_API_KEY = st.secrets["GEMINI_API_KEY"]
except:
    APP_PASSWORD = "123"
    MY_GEMINI_API_KEY = "임시"

GOOGLE_SHEET_NAME = "USP_추출기" 

if 'authenticated' not in st.session_state:
    st.session_state.authenticated = False

session_keys = [
    'analyzed', 'main_report_text', 'ad_plan_df', 'wc_img', 'ad_img', 
    'filename_base', 'main_url', 'worker_name', 'content_type', 'copy_style', 
    'user_ref_copy', 'extracted_img_url', 'extra_copies', 'final_compiled_text',
    'used_model_version', 'potential_imgs', 'product_name', 'p_code', 'compare_copy_list'
]
for key in session_keys:
    if key not in st.session_state:
        if key in ['extra_copies', 'potential_imgs', 'compare_copy_list']: st.session_state[key] = []
        elif key == 'ad_plan_df': st.session_state[key] = None
        elif 'img' in key: st.session_state[key] = None
        else: st.session_state[key] = ""

def check_password():
    if st.session_state.authenticated: return True
    col1, col2, col3 = st.columns([1, 2, 1])
    with col2:
        st.title("🔐 사내 전용 솔루션 접속")
        st.info("이 도구는 마케팅팀 전용 자산입니다. 비밀번호를 입력해주세요.")
        password_input = st.text_input("접속 비밀번호", type="password")
        if st.button("로그인", use_container_width=True):
            if password_input == APP_PASSWORD:
                st.session_state.authenticated = True
                st.rerun()
            else: st.error("🚨 비밀번호가 틀렸습니다.")
    return False

# ==========================================
# [표 변환 유틸리티 함수]
# ==========================================
def parse_md_table(md_text):
    lines = md_text.strip().split('\n')
    data, headers = [], []
    for line in lines:
        line = line.strip()
        if not line or '---' in line: continue
        if line.startswith('|') and line.endswith('|'):
            cols = [c.strip().replace('**', '') for c in line.split('|')[1:-1]]
            if not headers: headers = cols
            else: data.append(cols)
    if headers and data:
        for i in range(len(data)):
            if len(data[i]) < len(headers): data[i].extend([''] * (len(headers) - len(data[i])))
            elif len(data[i]) > len(headers): data[i] = data[i][:len(headers)]
        return pd.DataFrame(data, columns=headers)
    return None

def df_to_md_table(df):
    md = f"| {' | '.join(df.columns)} |\n"
    md += f"|{'|'.join(['---'] * len(df.columns))}|\n"
    for _, row in df.iterrows():
        md += f"| {' | '.join([str(x) for x in row.values])} |\n"
    return md

def create_default_ad_plan(p_name, url):
    return pd.DataFrame([
        ["광고 지면", "GFA 피드, 메인, 카카오 모먼트 등"], 
        ["제품명", p_name],
        ["URL", url],
        ["광고 카피", "(메인) / (서브)"],
        ["CTA", f"{p_name} 구매하기 >"],
        ["제작 설명", "우측 하단에 메인/서브 카피 텍스트 박스 배치"]
    ], columns=["구분", "내용"])

# ==========================================
# [2. 구글 시트 연동 엔진]
# ==========================================
def connect_google_spreadsheet():
    try:
        scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
        creds_json_str = st.secrets["GOOGLE_CREDENTIALS"]
        creds_dict = json.loads(creds_json_str)
        creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, scope)
        client = gspread.authorize(creds)
        return client.open(GOOGLE_SHEET_NAME) 
    except: return None

def save_to_google_sheet(data_list, worker_name):
    spreadsheet = connect_google_spreadsheet()
    if spreadsheet: 
        try:
            try: worksheet = spreadsheet.worksheet(worker_name)
            except gspread.WorksheetNotFound:
                worksheet = spreadsheet.add_worksheet(title=worker_name, rows="100", cols="10")
                worksheet.append_row(["날짜", "상품명", "상품코드", "URL", "분석결과"])
            worksheet.append_row(data_list)
        except: pass

# ==========================================
# [3. 데이터 수집 엔진]
# ==========================================
def get_data_bulldozer(target_url, max_pages=10):
    brand_text, review_list, pot_imgs, p_name = "", [], [], "상품명 수집 불가"
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
            p_name = soup.find('meta', property='og:title')['content'].strip() if soup.find('meta', property='og:title') else soup.title.text.strip()
            og_img = soup.find('meta', property='og:image')
            if og_img: pot_imgs.append(og_img['content'])
        except: pass

        service = Service("/usr/bin/chromedriver") 
        driver = webdriver.Chrome(service=service, options=options)
        driver.set_page_load_timeout(15)
        try:
            driver.get(target_url)
            time.sleep(2)
            brand_text = driver.find_element(By.TAG_NAME, 'body').text.strip()[:5000]
        except: pass

        if "xexymix.com" in target_url:
            p_code = parse_qs(urlparse(target_url).query).get('branduid', [''])[0]
            if p_code:
                enc_url = urllib.parse.quote(target_url, safe='')
                for page in range(1, max_pages + 1):
                    try:
                        driver.get(f"https://review4.cre.ma/v2/xexymix.com/product_reviews/list_v3?product_code={p_code}&parent_url={enc_url}&page={page}")
                        time.sleep(1.5)
                        content = driver.find_element(By.TAG_NAME, 'body').text.strip()
                        if len(content) < 50: break
                        review_list.append(content)
                    except: break
        else: review_list.append(brand_text[1000:4000])
    except Exception as e: pass
    finally:
        try: driver.quit()
        except: pass
    return brand_text, "\n".join(review_list)[:30000], pot_imgs, p_name 

# ==========================================
# [4. AI 분석 엔진] 
# ==========================================
def analyze_deep_usp_summarized(brand_text, review_text, pot_imgs, content_type, copy_style, product_url, product_name, user_ref_copy):
    
    copy_quality_rule = """
    [카피 작성 절대 규칙]
    - 안다르(andar)와 젝시믹스(XEXYMIX)의 베스트 카피처럼 트렌디하고 세련된 톤앤매너를 반드시 최우선으로 반영하세요.
    - '이 가격?', '세트 가격', '득템 기회', '역대급' 등 추상적이고 모호한 표현은 절대 금지합니다.
    - 정확한 구체적 금액(예: 2장에 29,900원) 대신 '1만원대'와 같이 체감가를 확 낮춰주는 범용적인 표현을 강제하여 사용하세요.
    - 모든 줄바꿈이 필요한 곳에는 <br> 대신 / 기호를 사용하세요.
    """

    if "명사/동사" in copy_style:
        ui_display_text = "*카피 텍스트 기준 '공백 포함 20자 이내 추출'*"
        ai_instruction = f"{copy_quality_rule}\n- 대괄호 [유형] 부분을 제외한 실제 카피 텍스트는 공백 포함 20자 이내로 압축하세요.\n- '~하다', '~되다', '~없다' 같은 딱딱한 서술어는 절대 금지합니다. 대신 '상쾌함 계속', '땀 냄새 걱정 NO', '믿고 구매' 처럼 세련된 명사나 짧은 단답형으로 임팩트 있게 종결하세요."
    elif "세일즈" in copy_style:
        ui_display_text = "*카피 텍스트 기준 '공백 포함 25자 이내 추출'*"
        ai_instruction = f"{copy_quality_rule}\n- 대괄호 [유형] 부분을 제외한 실제 카피 텍스트는 공백 포함 25자 이내로 작성하세요.\n- 혜택/할인을 강조하는 세일즈 후킹형으로 작성하며, 8개 중 최소 1개는 반드시 [가격 소구형]으로 작성할 것."
    else:
        ui_display_text = "*카피 텍스트 기준 '공백 포함 25자 이내 추출'*"
        ai_instruction = f"{copy_quality_rule}\n- 대괄호 [유형] 부분을 제외한 실제 카피 텍스트는 공백 포함 25자 이내로 작성하세요.\n- 고객에게 말 거는 듯한 자연스러운 서술형으로 작성할 것."
    
    ref_section = """
    [자사 베스트 카피 레퍼런스]
    - 입는 순간 -5kg, 마법의 슬림핏
    - 물놀이, 운동, 외출 올인원!
    - 남편이랑 아들이 서로 입겠다고 싸워요
    - 작년꺼 또 입어요..? 셔링 디테일로 핏이 달라지는
    """
    if user_ref_copy.strip(): ref_section += f"\n[캠페인 맞춤형 레퍼런스]\n{user_ref_copy}"

    kst_now = datetime.datetime.utcnow() + datetime.timedelta(hours=9)
    date_str = kst_now.strftime('%Y.%m.%d %H:%M')

    final_prompt = f"""
    # 지침 (매우 중요):
    1. 절대 인사말("안녕하세요" 등)이나 서론을 작성하지 마세요. 바로 본론으로 시작하세요.
    2. 출력물의 맨 첫 줄은 무조건 아래 포맷의 제목이어야 합니다.
    [{product_name} 핵심 USP & 후킹 카피 제안 / {date_str}]
    
    3. 1번(소구점 요약)과 2번(리뷰 요약) 항목은 제한 없이 가능한 한 많이(최소 5개 이상) 풍성하게 도출하세요.
    {ref_section}

    ---
    # Input Data:
    [상세페이지] {brand_text}
    [고객 리뷰] {review_text if len(review_text) > 50 else "리뷰 없음"}
    ---

    ### 🏢 1. 핵심 소구점 요약 (상세페이지 기반)
    *상세페이지에서 강조하는 제품의 차별화 포인트를 최소 5개 이상 최대한 많이 요약*
    1. **[소구점 1]**: (설명)
    2. **[소구점 2]**: (설명)
    ... (최소 5개 이상 가능한 많이 도출)

    ### 🗣️ 2. 고객의 '진짜 긍정' 리뷰 분석 (추천 이유)
    *실제 구매 고객들이 반복적으로 극찬하며 추천하는 포인트를 최소 5개 이상 최대한 많이 요약*
    1. **[추천 포인트 1]**: (고객 관점의 장점 요약)
    2. **[추천 포인트 2]**: (고객 관점의 장점 요약)
    ... (최소 5개 이상 가능한 많이 도출)
    * **[불편 해결]**: (이 제품을 통해 해결된 기존의 불편함)
    * **[대표 리뷰]**: (고객의 생생한 반응을 담은 한 마디)

    ### 🎯 3. 카피라이팅 추출 ({copy_style})
    {ui_display_text}
    *(AI 내부 지시사항: {ai_instruction})*
    
    1. [추천/만족형] (카피내용 작성)
    2. [시간 단축형] (카피내용 작성)
    3. [시각 보정형] (카피내용 작성)
    4. [피부/소재 공감형] (카피내용 작성)
    5. [가성비 증명형] (카피내용 작성)
    6. [상황 저격형] (카피내용 작성)
    7. [사회적 증거형] (카피내용 작성)
    8. [불만 해결형] (카피내용 작성)

    [SELECTED_IMAGE_URL]{pot_imgs[0] if pot_imgs else "None"}[/SELECTED_IMAGE_URL]
    [AD_PLAN_START]
    | 구분 | 내용 |
    |---|---|
    | 광고 지면 | GFA 피드, 메인, 카카오 모먼트 등 |
    | 제품명 | {product_name} |
    | URL | {product_url} |
    | 광고 카피 | (메인/서브 카피 2줄. 줄바꿈은 / 기호 사용) |
    | CTA | {product_name} 구매하기 > |
    | 제작 설명 | (디자인 지시사항) |
    [AD_PLAN_END]
    """

    client = genai.Client(api_key=MY_GEMINI_API_KEY)
    fallback_models = ['gemini-3.1-pro', 'gemini-3.1-flash', 'gemini-3.1-flash-lite', 'gemini-2.5-pro', 'gemini-2.5-flash']
    
    last_error = ""
    for model_name in fallback_models:
        try:
            response = client.models.generate_content(model=model_name, contents=final_prompt)
            return response.text, model_name
        except Exception as e:
            last_error = str(e)
            time.sleep(1)
            continue
            
    return f"🚨 [전체 서버 폭주] 10~20초 뒤 다시 시도해 주세요.\n상세 에러: {last_error}", None

# ==========================================
# [추가 카피 무한 생성기] 
# ==========================================
def generate_extra_copies(base_report, user_req, copy_style, user_ref_copy):
    base_rule = "안다르(andar)와 젝시믹스의 카피 톤앤매너 최우선 반영. '이 가격?', '득템' 같은 모호한 표현 강력 금지. 구체적 금액 대신 '1만원대'로 체감가 낮추는 표현 강제. 줄바꿈은 / 기호 사용."
    if "명사/동사" in copy_style: 
        ai_instruction = f"대괄호 [유형] 제외 순수 카피 기준 공백 포함 20자 이내. {base_rule} '~하다', '~되다', '~없다' 금지. '상쾌함 계속', '땀 냄새 걱정 NO', '믿고 구매' 등의 세련된 명사 단답형으로 종결할 것."
    elif "세일즈" in copy_style: 
        ai_instruction = f"대괄호 [유형] 제외 순수 카피 기준 공백 포함 25자 이내. {base_rule} 세일즈 후킹형 (최소 1개 [가격 소구형] 포함)."
    else: 
        ai_instruction = f"대괄호 [유형] 제외 순수 카피 기준 공백 포함 25자 이내. {base_rule} 자연스러운 서술형."

    prompt = f"""
    당신은 카피라이터입니다. 절대 서론이나 기존 분석내용을 재출력하지 마세요.
    오직 아래 [추가 요청사항]을 반영하여 새롭게 창작된 카피 8줄만 출력하세요.
    (형식: 1. [소구테마] 카피내용)
    
    [제품 USP 참고용]: {base_report[:2000]}
    [추가 요청사항]: {user_req}
    [규칙]: {ai_instruction}
    """
    client = genai.Client(api_key=MY_GEMINI_API_KEY)
    for attempt in range(5):
        for m in ['gemini-3.1-flash', 'gemini-2.5-flash', 'gemini-1.5-flash-latest']:
            try: return client.models.generate_content(model=m, contents=prompt).text
            except: time.sleep(1); continue
    return "🚨 서버 폭주로 추출에 실패했습니다. 잠시 후 다시 시도해주세요."

# ==========================================
# [비교 카피 추출기] 
# ==========================================
def generate_compare_copy(base_report, cmp_style):
    base_rule = "안다르(andar)와 젝시믹스의 카피 톤앤매너 최우선 반영. '이 가격?', '득템' 같은 모호한 표현 강력 금지. 구체적 금액 대신 '1만원대'로 체감가 낮추는 표현 강제. 줄바꿈은 / 기호 사용."
    if "명사/동사" in cmp_style: 
        ai_instruction = f"대괄호 [유형] 제외 순수 카피 기준 공백 포함 20자 이내. {base_rule} '~하다', '~되다', '~없다' 금지. '상쾌함 계속', '땀 냄새 걱정 NO', '믿고 구매' 등의 세련된 명사 단답형으로 종결할 것."
    elif "세일즈" in cmp_style: 
        ai_instruction = f"대괄호 [유형] 제외 순수 카피 기준 공백 포함 25자 이내. {base_rule} 세일즈 후킹형 (최소 1개 [가격 소구형] 포함)."
    else: 
        ai_instruction = f"대괄호 [유형] 제외 순수 카피 기준 공백 포함 25자 이내. {base_rule} 자연스러운 서술형."

    prompt = f"""
    당신은 카피라이터입니다. 서론 없이 리스트만 출력하세요.
    아래 [제품 USP 분석]을 바탕으로, '{cmp_style}' 스타일에 맞춰 매력적인 후킹 카피 8개를 도출하세요.
    
    [매우 중요한 규칙]
    1. 대괄호 [ ] 뒤에 "카피"라는 단어를 절대 적지 마세요! (예: 1. [추천/만족형] 시원해서 매일 입어요)
    2. 반드시 1. 2. 3. 숫자로 시작하는 리스트 형태로 출력하세요.
    3. {ai_instruction}
    
    [형식]
    1. [추천/만족형] (내용)
    2. [시간 단축형] (내용)
    ...
    8. [불만 해결형] (내용)
    
    [제품 USP 분석]: {base_report[:2000]}
    """
    client = genai.Client(api_key=MY_GEMINI_API_KEY)
    for attempt in range(5):
        for m in ['gemini-3.1-flash', 'gemini-2.5-flash']:
            try: return client.models.generate_content(model=m, contents=prompt).text
            except: time.sleep(1); continue
    return "🚨 추출 실패"

# ==========================================
# [5. 이미지 합성 (업로드 파일 전용, 필터 제거, 자동 폰트, CTA 추가)] 
# ==========================================
def create_ad_image(img_file, main_copy, sub_copy, cta_copy):
    if not img_file: return None
    try:
        img_bytes = img_file.getvalue()
        img = Image.open(io.BytesIO(img_bytes)).convert("RGBA")
        
        base_w = 1080
        h_size = int((float(img.size[1]) * (base_w / float(img.size[0]))))
        img = img.resize((base_w, h_size), Image.Resampling.LANCZOS)
        
        draw = ImageDraw.Draw(img)

        f_b, f_r = "NanumGothicBold.ttf", "NanumGothic.ttf"
        font_urls = {
            f_b: "https://hangeul.pstatic.net/hangeul_static/webfont/NanumGothic/NanumGothicBold.ttf",
            f_r: "https://hangeul.pstatic.net/hangeul_static/webfont/NanumGothic/NanumGothic.ttf"
        }
        for f_name, url in font_urls.items():
            if not os.path.exists(f_name):
                try: 
                    req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
                    with urllib.request.urlopen(req) as response, open(f_name, 'wb') as out_file:
                        out_file.write(response.read())
                except Exception as e: 
                    return f"ERROR: 폰트 다운로드 실패 ({str(e)})"
        
        # 폰트가 잘리지 않도록 텍스트 길이에 맞춰 사이즈 자동 조절 함수
        def get_fitted_font(text, font_path, max_size, max_width):
            size = max_size
            font = ImageFont.truetype(font_path, size)
            while size > 20:
                bbox = draw.textbbox((0, 0), text, font=font)
                if bbox[2] - bbox[0] <= max_width:
                    break
                size -= 2
                font = ImageFont.truetype(font_path, size)
            return font

        font_l = ImageFont.truetype(f_b, 35)
        # 로고 표시 (어떤 배경에서든 보이도록 검정 테두리 효과 추가)
        draw.text((50, 50), "X E X Y M I X", font=font_l, fill=(255,255,255,255), stroke_width=2, stroke_fill=(0,0,0,150))

        # 메인 및 서브 카피 폰트 피팅 (좌우 여백 80픽셀 기준)
        font_m = get_fitted_font(main_copy, f_b, 82, base_w - 80)
        font_s = get_fitted_font(sub_copy, f_r, 52, base_w - 80)

        # 필터 없이 카피가 잘 보이도록 그림자(Stroke) 처리 함수
        def draw_c(text, font, y):
            bbox = draw.textbbox((0,0), text, font=font)
            x = (base_w - (bbox[2] - bbox[0])) / 2
            draw.text((x, y), text, font=font, fill=(255,255,255,255), stroke_width=3, stroke_fill=(0,0,0,150))
            return bbox[3] - bbox[1]

        start_y = int(h_size * 0.65)
        h_s = draw_c(sub_copy, font_s, start_y)
        h_m = draw_c(main_copy, font_m, start_y + h_s + 20)
        
        # CTA 박스 추가 로직
        if cta_copy:
            cta_copy = cta_copy.replace("젝시믹스 -", "").replace("젝시믹스-", "").strip()
            
            # 하단 영역 이미지 색상을 분석하여 대비되는 박스 컬러 자동 결정
            from PIL import ImageStat
            crop = img.crop((0, int(h_size*0.7), base_w, h_size))
            stat = ImageStat.Stat(crop)
            avg_r, avg_g, avg_b = stat.mean[:3]
            
            # 빨강, 파랑, 초록, 검정 중 대비가 가장 큰 컬러 선정
            colors = [(220, 20, 60), (20, 60, 220), (34, 139, 34), (30, 30, 30)]
            max_dist = -1
            best_color = colors[3]
            for c in colors:
                dist = (c[0]-avg_r)**2 + (c[1]-avg_g)**2 + (c[2]-avg_b)**2
                if dist > max_dist:
                    max_dist = dist
                    best_color = c
            
            cta_font = get_fitted_font(cta_copy, f_b, 40, base_w - 150)
            cta_bbox = draw.textbbox((0,0), cta_copy, font=cta_font)
            cta_w = cta_bbox[2] - cta_bbox[0]
            cta_h = cta_bbox[3] - cta_bbox[1]
            
            box_w = cta_w + 80
            box_h = cta_h + 40
            box_x = (base_w - box_w) / 2
            box_y = start_y + h_s + 20 + h_m + 50
            
            # 박스가 하단을 넘어가면 위로 올리기
            if box_y + box_h > h_size - 20:
                box_y = h_size - box_h - 20
                
            draw.rounded_rectangle([box_x, box_y, box_x + box_w, box_y + box_h], radius=15, fill=best_color)
            
            tx = box_x + (box_w - cta_w) / 2
            ty = box_y + (box_h - cta_h) / 2 - 5
            draw.text((tx, ty), cta_copy, font=cta_font, fill=(255,255,255,255))

        buf = io.BytesIO()
        img.convert("RGB").save(buf, format="PNG")
        return buf.getvalue()
    except Exception as e: 
        return f"ERROR:{str(e)}"

def create_wordcloud_summary(text):
    try:
        client = genai.Client(api_key=MY_GEMINI_API_KEY)
        kw = client.models.generate_content(model='gemini-3.1-flash', contents=f"마케팅 핵심 키워드 50개 추출(콤마구분): {text[:5000]}").text
        wc = WordCloud(font_path="NanumGothic.ttf", width=600, height=400, background_color='white', colormap='tab10').generate(kw)
        buf = io.BytesIO()
        plt.figure(figsize=(6, 4)); plt.imshow(wc); plt.axis('off'); plt.tight_layout(pad=0)
        plt.savefig(buf, format='png', bbox_inches='tight', dpi=150); plt.close()
        return buf.getvalue()
    except: return None

# ==========================================
# [6. 메인 UI 렌더링]
# ==========================================
if check_password():
    st.title("🎯 마케팅 USP & 카피 자동 추출기 (V16.4 Finale)")
    st.markdown("---")

    tab1, tab2 = st.tabs(["🎯 새 분석 실행", "📜 히스토리"])

    with tab1:
        with st.sidebar:
            st.header("⚙️ 분석 설정")
            worker_input = st.text_input("👤 작업자 이름", placeholder="김마케터")
            st.markdown("---")
            content_type_input = st.selectbox("🎬 기획안 타겟", ["이미지+영상", "이미지", "영상", "USP만 추출"], index=3)
            copy_style_input = st.selectbox("✍️ 카피 스타일", ["명사/동사 임팩트형", "자연스러운 서술형", "USP + 세일즈 후킹형"], index=1)
            
            st.markdown("<p style='font-size:14px; font-weight:600; margin-bottom:0px;'>📝 캠페인 레퍼런스</p>", unsafe_allow_html=True)
            st.markdown("<p style='font-size:12px; color:gray; margin-top:0px; margin-bottom:5px;'>(선택 사항) 성과 좋았던 카피나 경쟁사 카피 레퍼런스를 넣어주면 반영한 카피가 추출됩니다.<br>미기재해도 추출에 문제 없습니다.</p>", unsafe_allow_html=True)
            user_ref_input = st.text_area("캠페인 레퍼런스", label_visibility="collapsed")
            
            st.markdown("---")
            st.markdown("<p style='font-size:14px; font-weight:600; margin-bottom:0px;'>🔗 상품 URL</p>", unsafe_allow_html=True)
            st.markdown("<p style='font-size:12px; color:gray; margin-top:0px; margin-bottom:5px;'>URL 입력 시 제품 코드 부분까지만 기입해 주세요<br>예: https://www.xexymix.com/shop/shopdetail.html?branduid=2077700</p>", unsafe_allow_html=True)
            main_url_input = st.text_input("상품 URL", label_visibility="collapsed")
            
            st.markdown("<br><p style='font-size:14px; font-weight:600; margin-bottom:0px;'>📜 리뷰 수집 범위(페이지)</p>", unsafe_allow_html=True)
            st.markdown("<p style='font-size:12px; color:gray; margin-top:0px; margin-bottom:5px;'>1페이지당 5개의 리뷰를 분석합니다 (10페이지=50개 리뷰 분석)</p>", unsafe_allow_html=True)
            max_pages_input = st.slider("리뷰 수집 범위", 1, 50, 1, label_visibility="collapsed")
        
        status_container = st.container()
        if st.button("▶ 분석 시작", type="primary", use_container_width=True):
            if not worker_input or not main_url_input:
                st.warning("⚠️ 이름과 URL을 모두 입력해 주세요.")
            else:
                with st.spinner("🚀 [작업 진행 중] AI가 상세페이지와 리뷰 데이터를 수집 및 분석하고 있습니다... (약 15~30초 소요)"):
                    st.session_state.content_type = content_type_input
                    st.session_state.main_url = main_url_input
                    st.session_state.worker_name = worker_input
                    st.session_state.copy_style = copy_style_input
                    st.session_state.compare_copy_list = [] 
                    
                    parsed = urlparse(main_url_input)
                    qs = parse_qs(parsed.query)
                    st.session_state.p_code = qs.get('branduid', qs.get('product_no', ['UNKNOWN']))[0]
                    
                    brand_txt, review_txt, pot_imgs, p_name = get_data_bulldozer(main_url_input, max_pages_input)
                    
                    # 🔥 제품명에서 젝시믹스 텍스트 원천 제거
                    p_name = p_name.replace("젝시믹스 - ", "").replace("젝시믹스-", "").strip()
                    st.session_state.product_name = p_name
                    
                    res_raw, model_used = analyze_deep_usp_summarized(brand_txt, review_txt, pot_imgs, content_type_input, copy_style_input, main_url_input, p_name, user_ref_input)
                    
                    if "🚨" not in res_raw:
                        st.session_state.used_model_version = model_used
                        
                        plan_m = re.search(r'\[AD_PLAN_START\](.*?)\[AD_PLAN_END\]', res_raw, re.DOTALL)
                        if plan_m:
                            st.session_state.ad_plan_df = parse_md_table(plan_m.group(1).strip())
                            res_raw = res_raw.replace(plan_m.group(0), "").replace("[AD_PLAN_START]", "").replace("[AD_PLAN_END]", "")
                        
                        if "이미지" in content_type_input and st.session_state.ad_plan_df is None:
                            st.session_state.ad_plan_df = create_default_ad_plan(p_name, main_url_input)
                        
                        res_raw = re.sub(r'\*\(AI 내부 지시사항.*?\)\*', '', res_raw, flags=re.DOTALL)
                        img_m = re.search(r'\[SELECTED_IMAGE_URL\](.*?)\[/SELECTED_IMAGE_URL\]', res_raw, re.DOTALL)
                        if img_m:
                            st.session_state.extracted_img_url = img_m.group(1).strip()
                            res_raw = res_raw.replace(img_m.group(0), "").replace("[SELECTED_IMAGE_URL]", "").replace("[/SELECTED_IMAGE_URL]", "")
                            
                        st.session_state.main_report_text = res_raw.strip()
                        st.session_state.wc_img = create_wordcloud_summary(review_txt)
                        st.session_state.analyzed = True
                        st.session_state.extra_copies = []
                        st.session_state.final_compiled_text = ""
                        
                        kst = datetime.datetime.utcnow() + datetime.timedelta(hours=9)
                        save_to_google_sheet([kst.strftime('%Y-%m-%d %H:%M'), p_name, st.session_state.p_code, main_url_input, res_raw], worker_input)
                        st.toast("✅ 분석 완료!")

        # ------------------------------------------
        # 결과 화면 (분할 렌더링)
        # ------------------------------------------
        if st.session_state.analyzed:
            st.markdown("---")
            st.markdown(f"<span style='font-size:13px; color:gray;'>(ver. {st.session_state.used_model_version})</span>", unsafe_allow_html=True)
            
            split_keyword = f"### 🎯 3. 카피라이팅 추출 ({st.session_state.copy_style})"
            
            if split_keyword in st.session_state.main_report_text:
                part12, part3_raw = st.session_state.main_report_text.split(split_keyword, 1)
                st.markdown(part12) 
                
                st.markdown("---")
                col_left, col_line, col_right = st.columns([1, 0.05, 1])
                
                with col_left:
                    st.markdown(f"### 🎯 3. 카피라이팅 추출 ({st.session_state.copy_style})")
                    st.markdown(part3_raw)
                    
                    st.markdown("<br>### 💡카피라이팅 추가 추출기", unsafe_allow_html=True)
                    ex_req = st.text_input("👇 원하는 소구점/무드를 입력하면 깔끔하게 8줄만 뽑아냅니다.", placeholder="예: 제품 USP 강조 + 11주년 혜택", key="ex_req_input")
                    if st.button("➕ 8개 추가", use_container_width=True):
                        if ex_req:
                            with st.spinner("⏳ 새로운 카피를 추출하는 중입니다..."):
                                new_c = generate_extra_copies(st.session_state.main_report_text, ex_req, st.session_state.copy_style, st.session_state.user_ref_copy)
                                st.session_state.extra_copies.append({"req": ex_req, "res": new_c})
                    
                    for idx, ex in enumerate(st.session_state.extra_copies):
                        with st.expander(f"💬 추가 추출 #{idx+1} (요청: {ex['req']})", expanded=True):
                            st.markdown(ex['res'])

                with col_line: 
                    st.markdown("<div class='vertical-line'></div>", unsafe_allow_html=True)
                    
                with col_right:
                    st.markdown("### 🔄 카피 스타일 비교 추출")
                    st.caption("기존 분석을 유지한 채 다른 스타일의 카피를 계속 누적하여 비교할 수 있습니다.")
                    cmp_style = st.selectbox("비교할 스타일 선택", ["USP + 세일즈 후킹형", "명사/동사 임팩트형", "자연스러운 서술형"], index=0)
                    if st.button("✨ 비교 추출하기", use_container_width=True):
                        with st.spinner("⏳ 비교용 카피를 추출하는 중입니다..."):
                            cmp_res = generate_compare_copy(st.session_state.main_report_text, cmp_style)
                            st.session_state.compare_copy_list.append({"style": cmp_style, "res": cmp_res})
                            
                    if st.session_state.compare_copy_list:
                        st.success("추출 완료!")
                        for idx, cmp in enumerate(reversed(st.session_state.compare_copy_list)):
                            with st.expander(f"🎯 3. 카피라이팅 추출 ({cmp['style']}) - #{len(st.session_state.compare_copy_list)-idx}", expanded=(idx==0)):
                                st.markdown(cmp['res'])
            else:
                st.markdown(st.session_state.main_report_text)
                st.markdown("<br>### 💡카피라이팅 추가 추출기", unsafe_allow_html=True)
                ex_req = st.text_input("👇 원하는 소구점/무드를 입력하면 깔끔하게 8줄만 뽑아냅니다. (예: 제품 USP 강조 + 11주년 혜택)")
                if st.button("➕ 8개 추가", use_container_width=True):
                    if ex_req:
                        with st.spinner("⏳ 새로운 카피를 추출하는 중입니다..."):
                            new_c = generate_extra_copies(st.session_state.main_report_text, ex_req, st.session_state.copy_style, st.session_state.user_ref_copy)
                            st.session_state.extra_copies.append({"req": ex_req, "res": new_c})
                for idx, ex in enumerate(st.session_state.extra_copies):
                    with st.expander(f"💬 추가 추출 #{idx+1} (요청: {ex['req']})", expanded=True):
                        st.markdown(ex['res'])

            # 기획안 영역 
            if "이미지" in st.session_state.content_type:
                st.markdown("<br>### 📋 1. 광고 소재 기획안 수정 (표 형식 유지)", unsafe_allow_html=True)
                with st.form("ad_plan_form"):
                    st.caption("👇 표의 칸을 클릭하여 내용을 수정한 뒤, 우측 하단의 **[💾 표 내용 저장]** 버튼을 누르세요.")
                    if st.session_state.ad_plan_df is not None:
                        edited_df = st.data_editor(st.session_state.ad_plan_df, use_container_width=True, hide_index=True)
                    else:
                        edited_df = None
                        st.warning("표 데이터가 없습니다.")
                    
                    if st.form_submit_button("💾 표 내용 저장"):
                        st.session_state.ad_plan_df = edited_df
                        st.success("표 수정 사항이 안전하게 저장되었습니다! (최종 합치기에 반영됨)")

                st.markdown("<br>### 🖼️ 2. 광고 시안 제작 (선택)", unsafe_allow_html=True)
                col_ad1, col_ad2 = st.columns(2)
                with col_ad1:
                    def_m, def_s, def_cta = "메인 카피 입력", "서브 카피 입력", "구매하기 >"
                    
                    # 🔥 추출된 기획안에서 메인, 서브, CTA를 가져와 디폴트 세팅
                    if st.session_state.ad_plan_df is not None:
                        copy_row = st.session_state.ad_plan_df[st.session_state.ad_plan_df["구분"].str.contains("카피", na=False)]
                        if not copy_row.empty:
                            full_copy = copy_row.iloc[0]["내용"]
                            if "/" in full_copy:
                                parts = full_copy.split("/", 1)
                                def_m, def_s = parts[0].strip(), parts[1].strip()
                            elif "<br>" in full_copy:
                                parts = full_copy.split("<br>", 1)
                                def_m, def_s = parts[0].strip(), parts[1].strip()
                            else: def_m = full_copy.strip()
                            
                        cta_row = st.session_state.ad_plan_df[st.session_state.ad_plan_df["구분"].str.contains("CTA", na=False)]
                        if not cta_row.empty:
                            def_cta = cta_row.iloc[0]["내용"].replace("젝시믹스 -", "").replace("젝시믹스-", "").strip()

                    m_c = st.text_input("합성할 메인 카피", value=def_m)
                    s_c = st.text_input("합성할 서브 카피", value=def_s)
                    c_c = st.text_input("합성할 CTA 카피", value=def_cta)
                    
                    st.markdown("**🖼️ 상품 이미지 업로드**")
                    st.caption("👇 **[Upload] 버튼을 클릭하여 시안 배경으로 사용할 상품 이미지를 직접 첨부해 주세요.**")
                    u_f = st.file_uploader("이미지 업로드 영역", label_visibility="collapsed", type=["jpg", "jpeg", "png"])
                    
                    if st.button("🖼️ 이미지 시안 생성"):
                        with st.spinner("⏳ 이미지 시안을 합성하는 중입니다... 잠시만 기다려주세요."):
                            if not u_f: 
                                st.warning("이미지를 먼저 업로드 해주세요.")
                            else: 
                                img_res = create_ad_image(u_f, m_c, s_c, c_c)
                                if isinstance(img_res, str) and img_res.startswith("ERROR:"):
                                    st.error(f"이미지 처리 중 오류가 발생했습니다: {img_res}")
                                elif img_res: 
                                    st.session_state.ad_img = img_res
                                    st.success("이미지 시안 생성 완료!")
                                else:
                                    st.error("알 수 없는 오류로 이미지를 생성하지 못했습니다.")
                                    
                with col_ad2:
                    if st.session_state.ad_img:
                        st.image(st.session_state.ad_img, caption="결과 미리보기 (1/4 축소)", width=300)
                        st.download_button("💾 시안 다운로드", data=st.session_state.ad_img, file_name="XEXY_AD_SAMPLE.png")

            st.markdown("<br>### ✅ 3. 최종 결과물 취합 및 복사", unsafe_allow_html=True)
            if st.session_state.wc_img:
                with st.expander("☁️ (참고) 리뷰 키워드 워드클라우드"): st.image(st.session_state.wc_img)
            
            if st.button("🚀 모든 내용 하나로 합치기", use_container_width=True):
                final = f"{st.session_state.main_report_text}\n\n"
                
                if st.session_state.compare_copy_list:
                    final += "[비교 추출된 카피 내역]\n"
                    for cmp in reversed(st.session_state.compare_copy_list):
                        final += f"▶ 스타일: {cmp['style']}\n{cmp['res']}\n\n"
                        
                if st.session_state.extra_copies:
                    final += "[추가 카피 적재 내역]\n"
                    for idx, ex in enumerate(st.session_state.extra_copies): final += f"▶ 요청: {ex['req']}\n{ex['res']}\n\n"
                if "이미지" in st.session_state.content_type and st.session_state.ad_plan_df is not None:
                    final += f"\n[광고 소재 기획안]\n{df_to_md_table(st.session_state.ad_plan_df)}"
                st.session_state.final_compiled_text = final
                
                kst = datetime.datetime.utcnow() + datetime.timedelta(hours=9) if 'timedelta' in globals() else datetime.datetime.utcnow() + datetime.timedelta(hours=9)
                save_to_google_sheet([kst.strftime('%Y-%m-%d %H:%M'), f"[최종 취합본] {st.session_state.product_name}", st.session_state.p_code, st.session_state.main_url, final], st.session_state.worker_name)
                st.success("✅ 최종 결과물이 정리되었으며, 구글 시트에도 안전하게 추가 적재되었습니다!")
            
            if st.session_state.final_compiled_text:
                st.text_area("📋 아래 박스 안을 클릭하고 Ctrl+A, Ctrl+C로 복사하세요.", st.session_state.final_compiled_text, height=350)

    with tab2:
        st.header("📋 과거 분석 히스토리 조회")
        ss = connect_google_spreadsheet()
        if ss:
            ws_names = [w.title for w in ss.worksheets()]
            sel_ws = st.selectbox("작업자 선택", ws_names)
            if sel_ws:
                st.dataframe(ss.worksheet(sel_ws).get_all_records(), use_container_width=True)

    st.markdown("<br><center>Internal Marketing Tool V16.4 (Perfectly Restored & Upgraded)</center>", unsafe_allow_html=True)
