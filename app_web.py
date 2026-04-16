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
matplotlib.use('Agg') # 🔥 클라우드 서버에서 이미지 생성 중 튕김 방지
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
st.set_page_config(page_title=""AI USP 추출 솔루션"", page_icon=""🎯"", layout=""wide"")

try:
    APP_PASSWORD = st.secrets[""APP_PASSWORD""] 
    MY_GEMINI_API_KEY = st.secrets[""GEMINI_API_KEY""]
except:
    APP_PASSWORD = ""123""
    MY_GEMINI_API_KEY = ""임시""

GOOGLE_SHEET_NAME = ""USP_추출기"" 

if 'authenticated' not in st.session_state:
    st.session_state.authenticated = False
for key in ['analyzed', 'final_report', 'wc_img', 'ad_img', 'filename_base', 'main_url', 'worker_name', 'content_type', 'copy_style']:
    if key not in st.session_state:
        st.session_state[key] = None if 'img' in key else """"

def check_password():
    if st.session_state.authenticated:
        return True
    col1, col2, col3 = st.columns([1, 2, 1])
    with col2:
        st.title(""🔐 사내 전용 솔루션 접속"")
        st.info(""이 도구는 마케팅팀 전용 자산입니다. 비밀번호를 입력해주세요."")
        password_input = st.text_input(""접속 비밀번호"", type=""password"")
        if st.button(""로그인"", use_container_width=True):
            if password_input == APP_PASSWORD:
                st.session_state.authenticated = True
                st.rerun()
            else:
                st.error(""🚨 비밀번호가 틀렸습니다."")
    return False

def connect_google_spreadsheet():
    try:
        scope = [""https://spreadsheets.google.com/feeds"", ""https://www.googleapis.com/auth/drive""]
        creds_json_str = st.secrets[""GOOGLE_CREDENTIALS""]
        creds_dict = json.loads(creds_json_str)
        creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, scope)
        client = gspread.authorize(creds)
        return client.open(GOOGLE_SHEET_NAME) 
    except Exception as e:
        st.error(f""🚨 구글 시트 연결 실패: {e}"")
        return None

def save_to_google_sheet(data_list, worker_name):
    spreadsheet = connect_google_spreadsheet()
    if spreadsheet: 
        try:
            try:
                worksheet = spreadsheet.worksheet(worker_name)
            except gspread.WorksheetNotFound:
                worksheet = spreadsheet.add_worksheet(title=worker_name, rows=""100"", cols=""10"")
                worksheet.append_row([""날짜"", ""상품명"", ""상품코드"", ""URL"", ""분석결과""])
            worksheet.append_row(data_list)
        except Exception as e:
            st.error(f""🚨 시트 기록 실패: {e}"")

# ==========================================
# [데이터 수집 엔진] 
# ==========================================
def get_data_bulldozer(target_url, max_pages=30):
    brand_text = """"
    review_list = []
    potential_product_imgs = [] 
    product_name = ""상품명 수집 불가"" 
    
    status_container.info(f""🚀 (1/3) 대상 서버 접속 및 데이터 수집 준비 중..."")
    
    options = Options()
    options.binary_location = ""/usr/bin/chromium"" 
    options.add_argument('--headless') 
    options.add_argument('--no-sandbox') 
    options.add_argument('--disable-dev-shm-usage') 
    options.add_argument('--disable-gpu')
    options.add_experimental_option(""prefs"", {""profile.managed_default_content_settings.images"": 2})
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

        service = Service(""/usr/bin/chromedriver"") 
        driver = webdriver.Chrome(service=service, options=options)
        driver.set_page_load_timeout(15)
        
        status_container.info(f""🚀 (1/3) 상세페이지 텍스트 및 상품명 수집 중..."")
        try:
            driver.get(target_url)
            time.sleep(2)
            if product_name == ""상품명 수집 불가"" or not product_name:
                product_name = driver.title
        except:
            try: driver.execute_script(""window.stop();"") 
            except: pass
            
        try:
            brand_text = driver.find_element(By.TAG_NAME, 'body').text.strip()[:5000]
        except:
            brand_text = ""상세페이지 텍스트 수집 실패""

        status_container.info(f""🤖 (2/3) 리뷰 수집 경로 분석 및 추출 중..."")
        if ""xexymix.com"" in target_url:
            parsed = urlparse(target_url)
            product_code = parse_qs(parsed.query).get('branduid', [''])[0]
            if product_code:
                encoded_url = urllib.parse.quote(target_url, safe='')
                for page in range(1, max_pages + 1):
                    try:
                        driver.get(f""https://review4.cre.ma/v2/xexymix.com/product_reviews/list_v3?product_code={product_code}&parent_url={encoded_url}&page={page}"")
                        time.sleep(2)
                        content = driver.find_element(By.TAG_NAME, 'body').text.strip()
                        if len(content) < 50: break
                        review_list.append(content)
                    except: pass
        else:
            review_list.append(brand_text[1000:4000])
            
    except Exception as e:
        status_container.error(f""⚠️ 브라우저 시스템 오류: {e}"")
    finally:
        try: driver.quit()
        except: pass
        
    return brand_text, ""\n"".join(review_list)[:30000], potential_product_imgs, product_name 

# ==========================================
# [AI 요약 & 동적 프롬프트 로직]
# ==========================================
def analyze_deep_usp_summarized(brand_text, review_text, potential_imgs, content_type, copy_style):
    status_container.info(f""🧠 (3/3) 제미나이 AI가 선택하신 옵션에 맞춰 맞춤형 기획안을 작성 중입니다..."")
    
    if ""명사/동사"" in copy_style:
        style_guide = ""모든 카피는 20자 이내로, '명사' 혹은 '동사'로 종결하여 이미지로 즉각 각인시킬 것.""
        copy_title = ""### 🎯 3. 초압축 다각도 후킹 카피 (각 20자 이내, 명사/동사 종결)""
    else:
        style_guide = ""모든 카피는 20자 이내로, 타겟 고객이 친근하게 느낄 수 있는 자연스러운 서술형(문장형)으로 자유롭게 작성할 것.""
        copy_title = ""### 🎯 3. 초압축 다각도 후킹 카피 (각 20자 이내, 자연스러운 자유 형식)""
    
    base_prompt = f""""""
    # Role: 시니어 커머스 전략가 (생활 밀착형 라이프스타일 큐레이터)
    
    # 분석 가이드라인:
    1. **절대 축약 금지**: 리뷰 분석(2번) 파트는 분량을 줄이지 말고 구체적이고 디테일하게 작성할 것. 
    2. **카피 스타일**: 오직 [3번 후킹 카피] 파트만 {style_guide}
    3. 오직 최종 결과 텍스트만 깔끔하게 출력하세요.

    ---
    # Input Data:
    [상세페이지 텍스트]
    {brand_text}
    
    [고객 리뷰 데이터 전량]
    {review_text if len(review_text) > 50 else ""현재 수집된 리뷰 데이터가 없습니다.""}
    ---

    ### 🏢 1. 5대 다각도 핵심 USP (경험 중심)
    1. **[관리/유지]**: 
    2. **[시각적 핏]**: 
    3. **[물성/촉감]**: 
    4. **[내구성]**: 
    5. **[상황 확산]**: 

    ### 🗣️ 2. 고객의 '진짜 생활' 리뷰 (Pain-Point 중심)
    * **[생활 밀착 키워드 Top 5]**: 
    * **[고객의 한 마디]**: 
    * **[해결된 불편함]**: 

    {copy_title}
    1. **[관리 혁명형]** 2. **[시간 단축형]** 3. **[시각 보정형]** 4. **[피부 공감형]** 5. **[가성비 증명형]** 6. **[상황 저격형]** 7. **[사회적 증거형]** 8. **[손실 방지형]** """"""

    image_prompt = """"
    if ""이미지"" in content_type:
        image_prompt = f""""""
    ### 💡 4. 소재 제작 기획안 (이미지)
    * **기획 의도**: (간략한 1~2줄 기획 의도)
    
    # [매우 중요] 광고 시안 합성을 위한 카피 및 이미지 선별:
    아래 [Potential Product Images] 리스트에서 실제 제품이나 모델이 가장 잘 드러난 사진 URL을 딱 1개만 골라주세요. (로고만 있으면 None)
    그리고 해당 이미지에 합성할 메인 카피와 서브 카피를 작성해주세요. (총 2~3줄 분량으로 짧게)
    
    [SELECTED_IMAGE_URL]이미지주소[/SELECTED_IMAGE_URL]
    [MAIN_COPY]시선을 끄는 메인 카피 (15자 이내)[/MAIN_COPY]
    [SUB_COPY]받쳐주는 서브 카피 (25자 이내)[/SUB_COPY]
    
    [Potential Product Images]
    {json.dumps(potential_imgs)}
    """"""

    video_prompt = """"
    if ""영상"" in content_type:
        video_num = ""5"" if ""이미지"" in content_type else ""4""
        video_prompt = f""""""
    ### 🎬 {video_num}. 숏폼 영상 기획안 (6초~15초 콘티)
    * **[초반 Hook (0~3초)]**: (구체적 상황 묘사)
    * **[Body 전개 (3~10초)]**: (시각적 대비 및 증명)
    * **[Action 마무리 (10~15초)]**: (구매 유도)
    """"""

    final_prompt = base_prompt + image_prompt + video_prompt

    try:
        client = genai.Client(api_key=MY_GEMINI_API_KEY)
        response = client.models.generate_content(model='gemini-2.5-flash', contents=final_prompt)
        return response.text
    except Exception as e:
        return f""AI 분석 실패: {e}""

# ==========================================
# [이미지 합성 및 워드클라우드 로직] 
# ==========================================
def create_ad_image(img_url, main_copy, sub_copy, product_url):
    if not img_url or img_url == ""None"": 
        return None
    try:
        req = urllib.request.Request(img_url, headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req) as response:
            img_data = response.read()
        img = Image.open(io.BytesIO(img_data)).convert(""RGBA"")

        base_width = 1080
        w_percent = (base_width / float(img.size[0]))
        h_size = int((float(img.size[1]) * float(w_percent)))
        img = img.resize((base_width, h_size), Image.Resampling.LANCZOS)

        overlay = Image.new('RGBA', img.size, (0, 0, 0, 0))
        draw = ImageDraw.Draw(overlay)
        
        box_top = int(h_size * 0.6)
        draw.rectangle(((0, box_top), (base_width, h_size)), fill=(0, 0, 0, 160))
        
        img = Image.alpha_composite(img, overlay)
        draw = ImageDraw.Draw(img)

        font_b_path = ""NanumGothicBold.ttf""
        font_r_path = ""NanumGothic.ttf""
        if not os.path.exists(font_b_path):
            urllib.request.urlretrieve(""https://github.com/google/fonts/raw/main/ofl/nanumgothic/NanumGothic-Bold.ttf"", font_b_path)
        if not os.path.exists(font_r_path):
            urllib.request.urlretrieve(""https://github.com/google/fonts/raw/main/ofl/nanumgothic/NanumGothic-Regular.ttf"", font_r_path)
            
        font_main = ImageFont.truetype(font_b_path, 65)
        font_sub = ImageFont.truetype(font_r_path, 40)
        font_url = ImageFont.truetype(font_r_path, 25)
        
        def draw_centered_text(text, font, y_pos, color):
            bbox = draw.textbbox((0, 0), text, font=font)
            text_x = (base_width - (bbox[2] - bbox[0])) / 2
            draw.text((text_x, y_pos), text, font=font, fill=color)

        start_y = box_top + 40
        
        main_copy = main_copy if main_copy else ""매력적인 메인 카피""
        draw_centered_text(main_copy, font_main, start_y, (255, 255, 255, 255))
        
        sub_copy = sub_copy if sub_copy else ""고객의 시선을 사로잡는 상세한 서브 카피""
        draw_centered_text(sub_copy, font_sub, start_y + 90, (220, 220, 220, 255))
        
        domain = urlparse(product_url).netloc
        draw_centered_text(f""Product link: {domain}"", font_url, h_size - 40, (150, 150, 150, 255))

        img_buffer = io.BytesIO()
        img.convert(""RGB"").save(img_buffer, format=""PNG"")
        return img_buffer.getvalue()
    except Exception as e: 
        return None

def create_wordcloud_summary(review_text):
    try:
        # 🔥 API 튕김 방지를 위해 2초간 숨을 고릅니다.
        time.sleep(2) 
        
        # 🔥 워드클라우드가 파싱 에러를 일으키지 않도록 프롬프트 강화
        wc_prompt = f""다음 대량의 리뷰에서 가장 많이 언급된 제품 장점 키워드(명사형) 50개만 추출해서, 다른 설명 일절 없이 오직 콤마(,)로만 구분해서 결과만 출력해.\n{review_text[:8000]}""
        client = genai.Client(api_key=MY_GEMINI_API_KEY)
        keywords = client.models.generate_content(model='gemini-2.5-flash', contents=wc_prompt).text
        
        font_path = ""NanumGothic.ttf""
        if not os.path.exists(font_path):
            urllib.request.urlretrieve(""https://github.com/google/fonts/raw/main/ofl/nanumgothic/NanumGothic-Regular.ttf"", font_path)
        
        wordcloud = WordCloud(font_path=font_path, width=800, height=800, background_color='white', colormap='magma').generate(keywords)
        img_buffer = io.BytesIO()
        plt.figure(figsize=(8, 8))
        plt.imshow(wordcloud, interpolation='bilinear')
        plt.axis('off')
        plt.savefig(img_buffer, format='png', bbox_inches='tight')
        plt.close()
        return img_buffer.getvalue()
    except Exception as e: 
        return None

# ==========================================
# [실제 화면 렌더링] 
# ==========================================
if check_password():
    col_t1, col_t2 = st.columns([9, 1])
    with col_t2:
        if st.button(""로그아웃""):
            st.session_state.authenticated = False
            st.rerun()

    st.title(""🎯 마케팅 USP & 카피 자동 추출기"")
    st.markdown(""---"")

    tab1, tab2 = st.tabs([""🎯 새 분석 실행"", ""📜 히스토리 보기""])

    with tab1:
        with st.sidebar:
            st.header(""설정"")
            worker_input = st.text_input(""👤 작업자 이름"", value="""", placeholder=""예: 김마케터"")
            st.markdown(""---"")
            
            content_type_input = st.selectbox(""🎬 기획안 타겟 선택"", [""이미지+영상"", ""이미지"", ""영상""], index=0)
            copy_style_input = st.selectbox(""✍️ 카피라이팅 스타일"", [""명사/동사 중심 (임팩트형)"", ""자유 형식 (자연스러운 서술형)""], index=0)
            st.markdown(""---"")
            
            main_url_input = st.text_input(""🔗 분석할 상품 URL"", value="""", placeholder=""URL을 입력하세요"")
            max_pages_input = st.slider(""📜 수집 페이지 수"", 10, 50, 30, 5)
        
        col_btn1, col_btn2, col_btn3 = st.columns([1, 1, 1])
        with col_btn2:
            start_btn = st.button(""▶ 분석 시작 및 시트 저장"", type=""primary"", use_container_width=True)

        status_container = st.container()

        if start_btn:
            if not worker_input or not main_url_input:
                st.warning(""⚠️ 이름과 URL을 모두 입력해주세요!"")
            else:
                with status_container:
                    brand_txt, review_txt, potential_imgs, product_name = get_data_bulldozer(main_url_input, max_pages_input)
                    
                    raw_report = analyze_deep_usp_summarized(brand_txt, review_txt, potential_imgs, content_type_input, copy_style_input)
                    
                    main_copy_text = """"
                    sub_copy_text = """"
                    selected_img_url = None
                    clean_report = raw_report
                    ad_img = None
                    
                    if ""이미지"" in content_type_input:
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
                            status_container.info(""🎨 AI가 선별한 상품 이미지에 추천 카피 합성 중..."")
                            ad_img = create_ad_image(selected_img_url, main_copy_text, sub_copy_text, main_url_input)

                    wc_img = None
                    if len(review_txt) >= 50:
                        wc_img = create_wordcloud_summary(review_txt)
                    
                    kst_now = datetime.datetime.utcnow() + datetime.timedelta(hours=9)
                    weekdays = ['월', '화', '수', '목', '금', '토', '일']
                    formatted_date = f""{kst_now.strftime('%Y-%m-%d')}({weekdays[kst_now.weekday()]}) {kst_now.strftime('%H:%M')}""
                    now_str = kst_now.strftime(""%Y%m%d_%H%M"")
                    
                    parsed = urlparse(main_url_input)
                    qs = parse_qs(parsed.query)
                    p_code = qs.get('branduid', qs.get('product_no', ['UNKNOWN']))[0]
                    
                    save_to_google_sheet([formatted_date, product_name, p_code, main_url_input, clean_report], worker_input)
                    
                    st.session_state.final_report = clean_report
                    st.session_state.wc_img = wc_img
                    st.session_state.ad_img = ad_img
                    st.session_state.filename_base = f""USP_{p_code}_{now_str}""
                    st.session_state.main_url = main_url_input
                    st.session_state.worker_name = worker_input
                    st.session_state.content_type = content_type_input 
                    st.session_state.copy_style = copy_style_input
                    st.session_state.analyzed = True
                    st.toast(""✅ 맞춤형 분석 및 기획 완료!"", icon=""🎉"")

        if st.session_state.analyzed:
            st.markdown(""---"")
            result_expander = st.expander(""📝 1. AI 맞춤형 기획안 & 카피 (클릭하여 열기)"", expanded=True)
            with result_expander:
                st.markdown(st.session_state.final_report)
                st.text_area(""📋 결과 복사하기"", st.session_state.final_report, height=400)

            if ""이미지"" in st.session_state.content_type:
                ad_expander = st.expander(""🖼️ 2. 추천 광고 소재 시안 (실제 상품 이미지 합성)"", expanded=True)
                with ad_expander:
                    if st.session_state.ad_img:
                        st.image(st.session_state.ad_img, caption=f""AI 텍스트 레이아웃 합성본 ({st.session_state.copy_style})"")
                        st.download_button(""💾 광고 시안(.png) 다운로드"", data=st.session_state.ad_img, file_name=f""AD_{st.session_state.filename_base}.png"", mime=""image/png"")
                    else:
                        st.warning(""적합한 상품 이미지를 찾지 못했습니다."")

            wordcloud_expander = st.expander(""☁️ 3. 리뷰 키워드 워드클라우드"", expanded=True)
            with wordcloud_expander:
                if st.session_state.wc_img:
                    st.image(st.session_state.wc_img, caption=""리뷰 핵심 키워드"")
                    st.download_button(""💾 워드클라우드(.png) 다운로드"", data=st.session_state.wc_img, file_name=f""WC_{st.session_state.filename_base}.png"", mime=""image/png"")
                else:
                    # 🔥 안내 문구를 오해 없도록 명확하게 수정했습니다!
                    st.markdown(""⚠️ 수집된 리뷰가 너무 적거나, 일시적인 AI 트래픽 과부하로 인해 워드클라우드 생성이 생략되었습니다."")
            
            st.download_button(
                label=""💾 전체 기획안(.txt) 일괄 다운로드"",
                data=f""분석 대상: {st.session_state.main_url}\n기획 타겟: {st.session_state.content_type}\n카피 스타일: {st.session_state.copy_style}\n작업자: {st.session_state.worker_name}\n==========================\n\n{st.session_state.final_report}"",
                file_name=f""{st.session_state.filename_base}.txt"",
                mime=""text/plain"",
                use_container_width=True
            )

    with tab2:
        st.header(""📋 과거 분석 히스토리"")
        spreadsheet = connect_google_spreadsheet()
        if spreadsheet:
            worksheets = spreadsheet.worksheets()
            selected_sheet = st.selectbox(""📂 조회할 작업자 탭 선택"", [ws.title for ws in worksheets])
            if selected_sheet:
                try:
                    data = spreadsheet.worksheet(selected_sheet).get_all_records()
                    if data:
                        st.dataframe(data, use_container_width=True)
                    else:
                        st.info(f""[{selected_sheet}] 탭에 아직 저장된 분석 내역이 없습니다."")
                except Exception as e:
                    st.warning(f""💡 [{selected_sheet}] 탭은 비어있거나 첫 줄(제목 행)이 없어서 표를 만들 수 없습니다. 새로운 분석을 1회 진행하시면 자동으로 채워집니다!"")

    st.markdown(""<br><center>마케팅 자동화 솔루션 | Internal Tool V11.1</center>"", unsafe_allow_html=True)
