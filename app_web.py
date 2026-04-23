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
# [1. 초기 세팅 및 세션 관리]
# ==========================================
st.set_page_config(page_title="AI USP 추출 솔루션", page_icon=":dart:", layout="wide")

try:
    APP_PASSWORD = st.secrets["APP_PASSWORD"] 
    MY_GEMINI_API_KEY = st.secrets["GEMINI_API_KEY"]
except:
    APP_PASSWORD = "123"
    MY_GEMINI_API_KEY = "임시"

GOOGLE_SHEET_NAME = "USP_추출기" 

# 세션 상태 초기화 (기존 작업 내역 유지 및 편집을 위함)
if 'authenticated' not in st.session_state:
    st.session_state.authenticated = False

session_keys = [
    'analyzed', 'main_report_text', 'ad_plan_text', 'wc_img', 'ad_img', 
    'filename_base', 'main_url', 'worker_name', 'content_type', 'copy_style', 
    'user_ref_copy', 'extracted_img_url', 'extra_copies', 'final_compiled_text'
]
for key in session_keys:
    if key not in st.session_state:
        if key == 'extra_copies': st.session_state[key] = []
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
            else:
                st.error("🚨 비밀번호가 틀렸습니다.")
    return False

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
# [3. 데이터 수집 엔진 (불도저)]
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
        # OG 및 기본 메타데이터 수집
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

        # Selenium을 이용한 동적 텍스트 수집
        service = Service("/usr/bin/chromedriver") 
        driver = webdriver.Chrome(service=service, options=options)
        driver.set_page_load_timeout(15)
        
        try:
            driver.get(target_url)
            time.sleep(2)
            brand_text = driver.find_element(By.TAG_NAME, 'body').text.strip()[:5000]
        except: pass

        # 젝시믹스 전용 크리마 리뷰 수집
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
        else:
            review_list.append(brand_text[1000:4000])
            
    except Exception as e:
        status_container.error(f"⚠️ 시스템 오류: {e}")
    finally:
        try: driver.quit()
        except: pass
        
    return brand_text, "\n".join(review_list)[:30000], pot_imgs[:20], p_name 

# ==========================================
# [4. AI 분석 엔진 (Waterfall Sequential Engine)]
# ==========================================
def analyze_deep_usp_summarized(brand_text, review_text, pot_imgs, content_type, copy_style, product_url, product_name, user_ref_copy):
    status_container.info(f"🧠 (3/3) AI 엔진이 워터폴(순차접속) 방식으로 기획안을 작성 중입니다...")
    
    style_guide = "20자 이내, 명사/동사 종결" if "명사/동사" in copy_style else "20자 이내, 자연스러운 서술형"
    
    ref_section = """
    [자사 베스트 카피 레퍼런스 (기본 톤앤매너)]
    - 입는 순간 -5kg, 마법의 슬림핏
    - 물놀이, 운동, 외출 올인원!
    - 남편이랑 아들이 서로 입겠다고 싸워요
    - 남편 주말 패션 구원템 등장!
    - 작년꺼 또 입어요..? 셔링 디테일로 핏이 달라지는
    """
    if user_ref_copy.strip():
        ref_section += f"\n[캠페인 맞춤형 레퍼런스 카피]\n{user_ref_copy}\n(위 레퍼런스의 '결'과 '말투'를 최우선으로 모방하여 작성할 것)"

    final_prompt = f"""
    # Role: 대한민국 최고 수준의 시니어 커머스 전략가 (젝시믹스 마케팅 전담)
    
    # 분석 가이드라인:
    1. **5포인트 분석**: 1번(소구점 요약)과 2번(리뷰 요약) 항목은 반드시 각각 5개씩 풍성하게 도출하세요.
    2. **긍정 우선**: 고객이 반복해서 추천하는 긍정적인 이유를 메인 USP로 잡되, 불만 해결 요소도 포함하여 균형을 맞추세요.
    3. **핵심 위주 축약**: 바쁜 실무자를 위해 의미 훼손 없는 선에서 짧고 강력한 문체로 작성하세요.
    {ref_section}

    ---
    # Input Data:
    [상세페이지 텍스트] {brand_text}
    [고객 리뷰 데이터] {review_text if len(review_text) > 50 else "리뷰 없음"}
    ---

    ### 🏢 1. 핵심 소구점 요약 (상세페이지 기반 5가지)
    *상세페이지에서 강조하는 제품의 차별화 포인트 5가지*
    1. **[포인트 1]**: (간략 명료하게 설명)
    2. **[포인트 2]**: (간략 명료하게 설명)
    3. **[포인트 3]**: (간략 명료하게 설명)
    4. **[포인트 4]**: (간략 명료하게 설명)
    5. **[포인트 5]**: (간략 명료하게 설명)

    ### 🗣️ 2. 고객의 '진짜 긍정' 리뷰 분석 (추천 이유 5가지)
    *실제 구매 고객들이 반복적으로 극찬하며 추천하는 포인트 5가지*
    1. **[추천 포인트 1]**: (고객 관점의 장점 요약)
    2. **[추천 포인트 2]**: (고객 관점의 장점 요약)
    3. **[추천 포인트 3]**: (고객 관점의 장점 요약)
    4. **[불편 해결]**: (이 제품을 통해 해결된 기존의 불편함)
    5. **[대표 리뷰]**: (고객의 생생한 반응을 담은 한 마디)

    ### 🎯 3. 초압축 다각도 후킹 카피 ({style_guide})
    1. [추천/만족형] 2. [시간 단축형] 3. [시각 보정형] 4. [피부/소재 공감형] 5. [가성비 증명형] 6. [상황 저격형] 7. [사회적 증거형] 8. [불만 해결형]

    # [광고 시안 및 기획안 데이터]
    [SELECTED_IMAGE_URL]{pot_imgs[0] if pot_imgs else "None"}[/SELECTED_IMAGE_URL]
    [AD_PLAN_START]
    | 구분 | 내용 |
    |---|---|
    | **광고 지면** | GFA 피드, 메인, 카카오 모먼트 등 |
    | **광고 텍스트** | (위에서 도출한 가장 매력적인 메인/서브 카피 2줄) |
    | **버튼(CTA)** | {product_name} > |
    | **URL** | {product_url} |
    | **제품명** | {product_name} |
    | **제작 설명** | - (레퍼런스 이미지 기반 배경 합성 및 레이아웃 지시사항) |
    [AD_PLAN_END]
    """

    client = genai.Client(api_key=MY_GEMINI_API_KEY)
    
    # 🔥 Waterfall Sequential Engine (6중 방어막)
    fallback_models = [
        'gemini-3.1-pro', 'gemini-3.1-flash', 'gemini-3.1-flash-lite', 
        'gemini-2.5-pro', 'gemini-2.5-flash', 'gemini-2.5-flash-lite'
    ]
    
    last_error = ""
    for i, model_name in enumerate(fallback_models):
        try:
            if i > 0:
                status_container.warning(f"⚠️ 이전 서버 혼잡으로 {model_name} 엔진으로 우회 접속을 시도합니다...")
            response = client.models.generate_content(model=model_name, contents=final_prompt)
            status_container.success(f"✅ {model_name} 엔진을 통해 분석에 성공했습니다!")
            return response.text
        except Exception as e:
            last_error = str(e)
            time.sleep(1.5)
            continue
            
    return f"🚨 [전체 서버 폭주] 6개의 모든 AI 엔진이 현재 응답할 수 없습니다. 10~20초 뒤 다시 시도해 주세요.\n상세 에러: {last_error}"

# 추가 카피 무한 적재 엔진 (워터폴 적용)
def generate_extra_copies(base_report, user_req, copy_style, user_ref_copy):
    style = "20자 이내 임팩트형" if "명사/동사" in copy_style else "20자 이내 서술형"
    prompt = f"다음 USP 분석을 기반으로 마케터의 추가 요청사항을 반영하여 카피 8개를 더 작성하라.\n분석내용: {base_report[:2000]}\n요청: {user_req}\n스타일: {style}\n레퍼런스: {user_ref_copy if user_ref_copy else '기본 브랜드 톤'}"
    
    client = genai.Client(api_key=MY_GEMINI_API_KEY)
    for model_name in ['gemini-3.1-flash', 'gemini-2.5-flash', 'gemini-1.5-flash-latest']:
        try:
            res = client.models.generate_content(model=model_name, contents=prompt)
            return res.text
        except: continue
    return "🚨 추가 추출 실패 (서버 혼잡)"

# ==========================================
# [5. 이미지 합성 및 워드클라우드 로직]
# ==========================================
def create_ad_image(img_source, main_copy, sub_copy, is_file=False):
    try:
        if is_file: img = Image.open(img_source).convert("RGBA")
        else:
            req = urllib.request.Request(img_source, headers={'User-Agent': 'Mozilla/5.0'})
            img = Image.open(io.BytesIO(urllib.request.urlopen(req).read())).convert("RGBA")

        base_w = 1080
        h_size = int((float(img.size[1]) * (base_w / float(img.size[0]))))
        img = img.resize((base_w, h_size), Image.Resampling.LANCZOS)
        
        # 젝시믹스 디자인 로직: 하단 95% 블랙 그라데이션
        overlay = Image.new('RGBA', img.size, (0,0,0,0))
        draw = ImageDraw.Draw(overlay)
        box_top = int(h_size * 0.40)
        for y in range(box_top, h_size):
            alpha = int(242 * (((y - box_top) / (h_size - box_top)) ** 0.45)) 
            draw.line([(0, y), (base_w, y)], fill=(0, 0, 0, alpha))
        
        img = Image.alpha_composite(img, overlay)
        draw = ImageDraw.Draw(img)

        # 폰트 다운로드 및 적용 (NanumGothic)
        f_b, f_r = "NanumGothicBold.ttf", "NanumGothic.ttf"
        for f in [f_b, f_r]:
            if not os.path.exists(f): 
                urllib.request.urlretrieve(f"https://github.com/google/fonts/raw/main/ofl/nanumgothic/{f}", f)
        
        font_m = ImageFont.truetype(f_b, 82); font_s = ImageFont.truetype(f_r, 52); font_l = ImageFont.truetype(f_b, 35)

        # 로고 및 카피 텍스트 배치
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
    except: return None

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
# [6. 메인 UI 및 워크플로우 렌더링]
# ==========================================
if check_password():
    st.title("🎯 마케팅 USP & 카피 자동 추출기 (V14.2 Waterfall)")
    st.markdown("---")

    tab1, tab2 = st.tabs(["🎯 새 분석 실행", "📜 히스토리"])

    with tab1:
        with st.sidebar:
            st.header("⚙️ 분석 설정")
            worker_input = st.text_input("👤 작업자 이름", placeholder="김마케터")
            st.markdown("---")
            content_type_input = st.selectbox("🎬 기획안 타겟", ["이미지+영상", "이미지", "영상", "USP만 추출"], index=3)
            copy_style_input = st.selectbox("✍️ 카피 스타일", ["명사/동사 임팩트형", "자연스러운 서술형"], index=1)
            user_ref_input = st.text_area("📝 캠페인 레퍼런스", placeholder="성과 좋았던 자사 카피들을 넣어주세요.")
            st.markdown("---")
            main_url_input = st.text_input("🔗 상품 URL", placeholder="URL 입력")
            st.caption("예시: https://www.xexymix.com/shop/shopdetail.html?branduid=2077700")
            max_pages_input = st.slider("📜 리뷰 수집 범위(페이지)", 10, 50, 10)
        
        status_container = st.container()
        if st.button("▶ 분석 시작 (서버 자동 우회)", type="primary", use_container_width=True):
            if not worker_input or not main_url_input:
                st.warning("⚠️ 이름과 URL을 모두 입력해 주세요.")
            else:
                with status_container:
                    # 데이터 수집 및 분석 시작
                    brand_txt, review_txt, pot_imgs, p_name = get_data_bulldozer(main_url_input, max_pages_input)
                    res_raw = analyze_deep_usp_summarized(brand_txt, review_txt, pot_imgs, content_type_input, copy_style_input, main_url_input, p_name, user_ref_input)
                    
                    if "🚨" not in res_raw:
                        # 기획안 표 추출
                        plan_m = re.search(r'\[AD_PLAN_START\](.*?)\[AD_PLAN_END\]', res_raw, re.DOTALL)
                        if plan_m:
                            st.session_state.ad_plan_text = plan_m.group(1).strip()
                            res_raw = res_raw.replace(plan_m.group(0), "")
                        # 이미지 URL 추출
                        img_m = re.search(r'\[SELECTED_IMAGE_URL\](.*?)\[/SELECTED_IMAGE_URL\]', res_raw, re.DOTALL)
                        if img_m:
                            st.session_state.extracted_img_url = img_m.group(1).strip()
                            res_raw = res_raw.replace(img_m.group(0), "")
                        
                        st.session_state.main_report_text = res_raw.strip()
                        st.session_state.wc_img = create_wordcloud_summary(review_txt)
                        st.session_state.analyzed = True
                        st.session_state.extra_copies = []
                        st.session_state.final_compiled_text = ""
                        
                        # 히스토리 저장
                        kst = datetime.datetime.utcnow() + datetime.timedelta(hours=9)
                        save_to_google_sheet([kst.strftime('%Y-%m-%d %H:%M'), p_name, "CODE", main_url_input, res_raw], worker_input)
                        st.toast("✅ 분석이 성공적으로 완료되었습니다!")

        # ------------------------------------------
        # 결과 화면 구성 (기존 기능 100% 유지)
        # ------------------------------------------
        if st.session_state.analyzed:
            st.markdown("---")
            st.markdown("### 📝 1. 핵심 USP & 후킹 카피 (5포인트 분석)")
            st.markdown(st.session_state.main_report_text)

            st.markdown("<br>### 💡 2. 카피라이팅 추가 추출기", unsafe_allow_html=True)
            col_ex1, col_ex2 = st.columns([4, 1])
            with col_ex1: ex_req = st.text_input("원하는 소구점/무드를 입력하면 8개를 더 뽑아줍니다.")
            with col_ex2:
                st.write("")
                if st.button("➕ 8개 추가", use_container_width=True):
                    if ex_req:
                        with st.spinner("AI가 마케터의 의도를 반영하는 중..."):
                            new_c = generate_extra_copies(st.session_state.main_report_text, ex_req, copy_style_input, user_ref_input)
                            st.session_state.extra_copies.append({"req": ex_req, "res": new_c})
            
            for ex in st.session_state.extra_copies:
                with st.expander(f"💬 추가 추출 (요청: {ex['req']})", expanded=True):
                    st.markdown(ex['res'])

            st.markdown("<br>### 📋 3. 광고 소재 기획안 수정 (표 형식 유지)", unsafe_allow_html=True)
            if "이미지" in content_type_input:
                # 🔥 기획안 편집창: 표의 Markdown 형식을 그대로 유지한 채 편집
                st.session_state.ad_plan_text = st.text_area("아래 표 내용을 수정하세요. (표의 '||' 구분선은 유지해 주세요)", st.session_state.ad_plan_text, height=250)
                st.markdown(st.session_state.ad_plan_text) # 수정된 표 실시간 미리보기
                
                st.markdown("<br>### 🖼️ 4. 광고 시안 제작 (선택)", unsafe_allow_html=True)
                col_ad1, col_ad2 = st.columns(2)
                with col_ad1:
                    ad_mode = st.radio("시안 제작 방식 선택", ["🤖 AI 추천 이미지", "📁 직접 사진 업로드"])
                    m_c = st.text_input("합성할 메인 카피")
                    s_c = st.text_input("합성할 서브 카피")
                    u_f = st.file_uploader("사진 파일 (직접 업로드 시)") if ad_mode == "📁 직접 사진 업로드" else None
                    if st.button("🖼️ 이미지 시안 생성"):
                        src = u_f if ad_mode == "📁 직접 사진 업로드" else st.session_state.extracted_img_url
                        st.session_state.ad_img = create_ad_image(src, m_c, s_c, is_file=(ad_mode=="📁 직접 사진 업로드"))
                with col_ad2:
                    if st.session_state.ad_img:
                        st.image(st.session_state.ad_img, caption="결과 미리보기 (1/4 축소)", width=300)
                        st.download_button("💾 시안 다운로드", data=st.session_state.ad_img, file_name="XEXY_AD_SAMPLE.png")

            st.markdown("<br>### ✅ 5. 최종 결과물 취합 및 복사", unsafe_allow_html=True)
            if st.session_state.wc_img:
                with st.expander("☁️ (참고) 리뷰 키워드 워드클라우드"): st.image(st.session_state.wc_img)
            
            if st.button("🚀 모든 내용 하나로 합치기", use_container_width=True):
                final = f"분석 상품: {main_url_input}\n\n[1. 메인 USP 분석]\n{st.session_state.main_report_text}\n\n"
                if st.session_state.extra_copies:
                    final += "[2. 추가 카피 적재 내역]\n"
                    for ex in st.session_state.extra_copies: final += f"▶요청: {ex['req']}\n{ex['res']}\n"
                if "이미지" in content_type_input:
                    final += f"\n[3. 최종 수정된 기획안]\n{st.session_state.ad_plan_text}"
                st.session_state.final_compiled_text = final
            
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

    st.markdown("<br><center>Internal Marketing Tool V14.2 (Waterfall Sequential Engine)</center>", unsafe_allow_html=True)
