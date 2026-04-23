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

# 🔥 텍스트 에어리어 Placeholder 폰트 사이즈 조절 CSS
st.markdown("""
    <style>
    textarea::placeholder {
        font-size: 13px !important;
    }
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
    'used_model_version', 'potential_imgs', 'product_name'
]
for key in session_keys:
    if key not in st.session_state:
        if key == 'extra_copies' or key == 'potential_imgs': st.session_state[key] = []
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
    data = []
    headers = []
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
        ["광고 카피", "(메인)\n(서브)"],
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
    status_container.info(f"🚀 (1/3) 대상 서버 접속 및 데이터 수집 중...")
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
            for img in soup.find_all('img'):
                src = img.get('src', '') or img.get('data-src', '')
                if not src or any(x in src.lower() for x in ['logo', 'icon', 'btn', 'gif']): continue
                if src.startswith('//'): src = 'https:' + src
                elif src.startswith('/'): src = f"https://{urlparse(target_url).netloc}{src}"
                if src not in pot_imgs: pot_imgs.append(src)
        except: pass

        service = Service("/usr/bin/chromedriver") 
        driver = webdriver.Chrome(service=service, options=options)
        driver.set_page_load_timeout(15)
        try:
            driver.get(target_url)
            time.sleep(2)
            brand_text = driver.find_element(By.TAG_NAME, 'body').text.strip()[:5000]
        except: pass

        status_container.info(f"🤖 (2/3) 고객 리뷰 분석 및 수집 중 (최대 {max_pages}P)...")
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
    except Exception as e: status_container.error(f"⚠️ 시스템 오류: {e}")
    finally:
        try: driver.quit()
        except: pass
    return brand_text, "\n".join(review_list)[:30000], pot_imgs[:20], p_name 

# ==========================================
# [4. AI 분석 엔진]
# ==========================================
def analyze_deep_usp_summarized(brand_text, review_text, pot_imgs, content_type, copy_style, product_url, product_name, user_ref_copy):
    status_container.info(f"🧠 (3/3) AI가 핵심 USP를 도출하고 있습니다...")
    
    # 🔥 카피 스타일 옵션 추가 반영
    if "명사/동사" in copy_style:
        style_guide = "20자 이내, 명사/동사 종결"
    elif "세일즈" in copy_style:
        style_guide = "20자 이내, 제품의 핵심 USP와 할인/혜택 등을 강조하여 당장 구매하고 싶게 만드는 세일즈 후킹형"
    else:
        style_guide = "20자 이내, 자연스러운 서술형"
    
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
    1. **[소구점 1]**: (간략 명료하게 설명)
    2. **[소구점 2]**: (간략 명료하게 설명)
    ... (최소 5개 이상 가능한 많이 도출)

    ### 🗣️ 2. 고객의 '진짜 긍정' 리뷰 분석 (추천 이유)
    *실제 구매 고객들이 반복적으로 극찬하며 추천하는 포인트를 최소 5개 이상 최대한 많이 요약*
    1. **[추천 포인트 1]**: (고객 관점의 장점 요약)
    2. **[추천 포인트 2]**: (고객 관점의 장점 요약)
    ... (최소 5개 이상 가능한 많이 도출)
    * **[불편 해결]**: (이 제품을 통해 해결된 기존의 불편함)
    * **[대표 리뷰]**: (고객의 생생한 반응을 담은 한 마디)

    ### 🎯 3. 초압축 다각도 후킹 카피 ({style_guide})
    1. [추천/만족형] 2. [시간 단축형] 3. [시각 보정형] 4. [피부/소재 공감형] 5. [가성비 증명형] 6. [상황 저격형] 7. [사회적 증거형] 8. [불만 해결형]

    [SELECTED_IMAGE_URL]{pot_imgs[0] if pot_imgs else "None"}[/SELECTED_IMAGE_URL]
    [AD_PLAN_START]
    | 구분 | 내용 |
    |---|---|
    | 광고 지면 | GFA 피드, 메인, 카카오 모먼트 등 |
    | 제품명 | {product_name} |
    | URL | {product_url} |
    | 광고 카피 | (메인/서브 카피 2줄) |
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
    if "명사/동사" in copy_style:
        style = "각 20자 이내, 임팩트형(명사/동사 종결)"
    elif "세일즈" in copy_style:
        style = "각 20자 이내, 제품의 핵심 USP와 혜택을 강조하는 세일즈 후킹형"
    else:
        style = "각 20자 이내, 자연스러운 서술형"

    prompt = f"""
    당신은 카피라이터입니다. 절대 서론이나 기존 분석내용을 재출력하지 마세요.
    오직 아래 [추가 요청사항]을 반영하여 새롭게 창작된 카피 8줄만 출력하세요.
    (형식: 1. [소구테마] 카피내용)
    
    [제품 USP 참고용]: {base_report[:2000]}
    [추가 요청사항]: {user_req}
    [스타일]: {style}
    """
    client = genai.Client(api_key=MY_GEMINI_API_KEY)
    for model_name in ['gemini-3.1-flash', 'gemini-2.5-flash', 'gemini-1.5-flash-latest']:
        try:
            return client.models.generate_content(model=model_name, contents=prompt).text
        except: time.sleep(0.5); continue
    return "🚨 서버 지연. 잠시 후 시도하세요."

# ==========================================
# [5. 이미지 합성 및 워드클라우드 로직]
# ==========================================
def create_ad_image(img_source, main_copy, sub_copy, is_file=False):
    if not img_source or img_source == "None" or str(img_source).strip() == "": return None
    try:
        if is_file: img = Image.open(img_source).convert("RGBA")
        else:
            clean_url = img_source.strip(' \n"\'[]')
            req = urllib.request.Request(clean_url, headers={'User-Agent': 'Mozilla/5.0'})
            img = Image.open(io.BytesIO(urllib.request.urlopen(req).read())).convert("RGBA")

        base_w = 1080
        h_size = int((float(img.size[1]) * (base_w / float(img.size[0]))))
        img = img.resize((base_w, h_size), Image.Resampling.LANCZOS)
        
        overlay = Image.new('RGBA', img.size, (0,0,0,0))
        draw = ImageDraw.Draw(overlay)
        box_top = int(h_size * 0.40)
        for y in range(box_top, h_size):
            alpha = int(242 * (((y - box_top) / (h_size - box_top)) ** 0.45)) 
            draw.line([(0, y), (base_w, y)], fill=(0, 0, 0, alpha))
        
        img = Image.alpha_composite(img, overlay)
        draw = ImageDraw.Draw(img)

        f_b, f_r = "NanumGothicBold.ttf", "NanumGothic.ttf"
        for f in [f_b, f_r]:
            if not os.path.exists(f): urllib.request.urlretrieve(f"https://github.com/google/fonts/raw/main/ofl/nanumgothic/{f}", f)
        
        font_m = ImageFont.truetype(f_b, 82); font_s = ImageFont.truetype(f_r, 52); font_l = ImageFont.truetype(f_b, 35)

        draw.text((50, 50), "X E X Y M I X", font=font_l, fill=(255,255,255,255))
        def draw_c(text, font, y):
            x = (base_w - draw.textbbox((0,0), text, font=font)[2]) / 2
            draw.text((x, y), text, font=font, fill=(255,255,255,255))

        start_y = int(h_size * 0.65)
        draw_c(sub_copy, font_s, start_y)
        draw_c(main_copy, font_m, start_y + 90)
        
        buf = io.BytesIO()
        img.convert("RGB").save(buf, format="PNG")
        return buf.getvalue()
    except Exception as e: return None

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
    st.title("🎯 마케팅 USP & 카피 자동 추출기 (V14.7 Finale)")
    st.markdown("---")

    tab1, tab2 = st.tabs(["🎯 새 분석 실행", "📜 히스토리"])

    with tab1:
        with st.sidebar:
            st.header("⚙️ 분석 설정")
            worker_input = st.text_input("👤 작업자 이름", placeholder="김마케터")
            st.markdown("---")
            content_type_input = st.selectbox("🎬 기획안 타겟", ["이미지+영상", "이미지", "영상", "USP만 추출"], index=3)
            copy_style_input = st.selectbox("✍️ 카피 스타일", ["명사/동사 임팩트형", "자연스러운 서술형", "USP + 세일즈 후킹형"], index=1)
            
            # 🔥 Placeholder 변경 및 폰트 사이즈(CSS) 적용 
            user_ref_input = st.text_area("📝 캠페인 레퍼런스", placeholder="성과 좋았던 카피나 경쟁사 카피 레퍼런스를 넣어주면 반영한 카피가 추출됩니다.")
            st.markdown("---")
            
            main_url_input = st.text_input("🔗 상품 URL", placeholder="URL 입력")
            st.caption("URL 입력 시 제품 코드 부분까지만 기입해 주세요\n예: https://www.xexymix.com/shop/shopdetail.html?branduid=2077700")
            
            max_pages_input = st.slider("📜 리뷰 수집 범위(페이지)", 10, 50, 10)
            st.caption("1페이지당 5개의 리뷰를 분석합니다 (10페이지=50개 리뷰 분석)")
        
        status_container = st.container()
        if st.button("▶ 분석 시작", type="primary", use_container_width=True):
            if not worker_input or not main_url_input:
                st.warning("⚠️ 이름과 URL을 모두 입력해 주세요.")
            else:
                with status_container:
                    st.session_state.content_type = content_type_input
                    st.session_state.main_url = main_url_input
                    st.session_state.worker_name = worker_input
                    st.session_state.copy_style = copy_style_input
                    
                    brand_txt, review_txt, pot_imgs, p_name = get_data_bulldozer(main_url_input, max_pages_input)
                    st.session_state.potential_imgs = pot_imgs
                    st.session_state.product_name = p_name # 상품명 저장
                    
                    res_raw, model_used = analyze_deep_usp_summarized(brand_txt, review_txt, pot_imgs, content_type_input, copy_style_input, main_url_input, p_name, user_ref_input)
                    
                    if "🚨" not in res_raw:
                        st.session_state.used_model_version = model_used
                        
                        plan_m = re.search(r'\[AD_PLAN_START\](.*?)\[AD_PLAN_END\]', res_raw, re.DOTALL)
                        if plan_m:
                            st.session_state.ad_plan_df = parse_md_table(plan_m.group(1).strip())
                            res_raw = res_raw.replace(plan_m.group(0), "").replace("[AD_PLAN_START]", "").replace("[AD_PLAN_END]", "")
                        
                        if "이미지" in content_type_input and st.session_state.ad_plan_df is None:
                            st.session_state.ad_plan_df = create_default_ad_plan(p_name, main_url_input)
                        
                        img_m = re.search(r'\[SELECTED_IMAGE_URL\](.*?)\[/SELECTED_IMAGE_URL\]', res_raw, re.DOTALL)
                        if img_m:
                            st.session_state.extracted_img_url = img_m.group(1).strip()
                            res_raw = res_raw.replace(img_m.group(0), "").replace("[SELECTED_IMAGE_URL]", "").replace("[/SELECTED_IMAGE_URL]", "")
                        
                        st.session_state.main_report_text = res_raw.strip()
                        st.session_state.wc_img = create_wordcloud_summary(review_txt)
                        st.session_state.analyzed = True
                        st.session_state.extra_copies = []
                        st.session_state.final_compiled_text = ""
                        
                        # 1차 분석 시트 저장
                        kst = datetime.datetime.utcnow() + datetime.timedelta(hours=9)
                        save_to_google_sheet([kst.strftime('%Y-%m-%d %H:%M'), p_name, "CODE", main_url_input, res_raw], worker_input)
                        st.toast("✅ 분석 완료!")

        # ------------------------------------------
        # 결과 화면
        # ------------------------------------------
        if st.session_state.analyzed:
            st.markdown("---")
            st.markdown(f"<span style='font-size:13px; color:gray;'>(ver. {st.session_state.used_model_version})</span>", unsafe_allow_html=True)
            st.markdown(st.session_state.main_report_text)

            st.markdown("<br>### 💡카피라이팅 추가 추출기", unsafe_allow_html=True)
            col_ex1, col_ex2 = st.columns([4, 1])
            with col_ex1: ex_req = st.text_input("👇 원하는 소구점/무드를 입력하면 깔끔하게 8줄만 뽑아냅니다. (예: 제품 USP 강조 + 11주년 혜택)")
            with col_ex2:
                st.write("")
                if st.button("➕ 8개 추가", use_container_width=True):
                    if ex_req:
                        with st.spinner("새로운 카피 추출 중..."):
                            new_c = generate_extra_copies(st.session_state.main_report_text, ex_req, st.session_state.copy_style, st.session_state.user_ref_copy)
                            st.session_state.extra_copies.append({"req": ex_req, "res": new_c})
            
            for idx, ex in enumerate(st.session_state.extra_copies):
                with st.expander(f"💬 추가 추출 #{idx+1} (요청: {ex['req']})", expanded=True):
                    st.markdown(ex['res'])

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
                    ad_mode = st.radio("시안 제작 방식", ["🤖 AI 추출 이미지", "📁 직접 사진 업로드"])
                    m_c = st.text_input("합성할 메인 카피")
                    s_c = st.text_input("합성할 서브 카피")
                    u_f = st.file_uploader("사진 파일") if ad_mode == "📁 직접 사진 업로드" else None
                    
                    if st.button("🖼️ 이미지 시안 생성"):
                        # 🔥 이미지 생성 시 뚜렷한 로딩 피드백 (스피너) 적용
                        with st.spinner("⏳ 이미지 시안을 생성하는 중입니다... 화면을 이동하지 마세요."):
                            src = u_f if ad_mode == "📁 직접 사진 업로드" else st.session_state.extracted_img_url
                            
                            if (not src or src == "None") and ad_mode == "🤖 AI 추출 이미지":
                                src = st.session_state.potential_imgs[0] if st.session_state.potential_imgs else None
                                
                            if not src: 
                                st.warning("적합한 이미지를 찾지 못했습니다. 직접 업로드 방식을 사용해주세요.")
                            else: 
                                st.session_state.ad_img = create_ad_image(src, m_c, s_c, is_file=(ad_mode=="📁 직접 사진 업로드"))
                                if st.session_state.ad_img is None:
                                    st.error("이미지 생성에 실패했습니다. 이미지 URL 문제일 수 있으니 직접 업로드해보세요.")
                                else:
                                    st.success("이미지 시안 생성 완료!")
                                    
                with col_ad2:
                    if st.session_state.ad_img:
                        st.image(st.session_state.ad_img, caption="결과 미리보기 (1/4 축소)", width=300)
                        st.download_button("💾 시안 다운로드", data=st.session_state.ad_img, file_name="XEXY_AD_SAMPLE.png")

            st.markdown("<br>### ✅ 3. 최종 결과물 취합 및 복사", unsafe_allow_html=True)
            if st.session_state.wc_img:
                with st.expander("☁️ (참고) 리뷰 키워드 워드클라우드"): st.image(st.session_state.wc_img)
            
            if st.button("🚀 모든 내용 하나로 합치기", use_container_width=True):
                final = f"{st.session_state.main_report_text}\n\n"
                if st.session_state.extra_copies:
                    final += "[추가 카피 적재 내역]\n"
                    for idx, ex in enumerate(st.session_state.extra_copies): final += f"▶ 요청: {ex['req']}\n{ex['res']}\n\n"
                if "이미지" in st.session_state.content_type and st.session_state.ad_plan_df is not None:
                    final += f"\n[광고 소재 기획안]\n{df_to_md_table(st.session_state.ad_plan_df)}"
                st.session_state.final_compiled_text = final
                
                # 🔥 최종본 시트 2차 적재 (합치기 버튼 누를 때)
                kst = datetime.datetime.utcnow() + datetime.timedelta(hours=9)
                save_to_google_sheet([kst.strftime('%Y-%m-%d %H:%M'), f"[최종 취합본] {st.session_state.product_name}", "FINAL", st.session_state.main_url, final], st.session_state.worker_name)
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

    st.markdown("<br><center>Internal Marketing Tool V14.7 (UX Optimized)</center>", unsafe_allow_html=True)
