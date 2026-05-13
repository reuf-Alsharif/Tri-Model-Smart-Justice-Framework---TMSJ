"""
سيرفر ويب لسحب أحكام المحكمة التجارية
========================================
- دعم متعدد المستخدمين (Multi-session)
- معالجة متوازية للسرعة
- استخدام موارد محسّن
- نظام تسجيل دخول
"""

from flask import Flask, render_template, request, jsonify, Response, send_file, session, redirect, url_for
from playwright.sync_api import sync_playwright
from bs4 import BeautifulSoup
import requests as http_requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from concurrent.futures import ThreadPoolExecutor, as_completed
from functools import wraps
import threading
import json
import time
import os
import uuid
import queue
import hashlib
import re

app = Flask(__name__)
app.secret_key = os.urandom(24)

# ============ ملف المستخدمين ============
USERS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "users.json")

def load_users():
    """تحميل بيانات المستخدمين"""
    if os.path.exists(USERS_FILE):
        with open(USERS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {"users": {}}

def save_users(data):
    """حفظ بيانات المستخدمين"""
    with open(USERS_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

def hash_password(password):
    """تشفير كلمة المرور"""
    return hashlib.sha256(password.encode()).hexdigest()

def login_required(f):
    """ديكوراتور للتحقق من تسجيل الدخول"""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user' not in session:
            # إذا كان الطلب AJAX/API، نرجع JSON بدل redirect
            if request.headers.get('X-Requested-With') == 'XMLHttpRequest' or \
               request.path.startswith('/api/') or \
               request.accept_mimetypes.best == 'application/json':
                return jsonify({"error": "غير مسجل دخول", "login_required": True}), 401
            return redirect(url_for('login_page'))
        return f(*args, **kwargs)
    return decorated_function

def add_file_to_user(username, filename):
    """إضافة ملف لقائمة ملفات المستخدم"""
    data = load_users()
    if username in data["users"]:
        if "files" not in data["users"][username]:
            data["users"][username]["files"] = []
        if filename not in data["users"][username]["files"]:
            data["users"][username]["files"].append(filename)
            save_users(data)

def get_user_files(username):
    """جلب قائمة ملفات المستخدم"""
    data = load_users()
    if username in data["users"]:
        return data["users"][username].get("files", [])
    return []

def save_failed_ids(username, session_id, failed_ids, output_file):
    """حفظ المعرّفات الفاشلة للمستخدم"""
    data = load_users()
    if username in data["users"]:
        if "pending_jobs" not in data["users"][username]:
            data["users"][username]["pending_jobs"] = {}
        data["users"][username]["pending_jobs"][session_id] = {
            "failed_ids": failed_ids,
            "output_file": output_file,
            "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "count": len(failed_ids)
        }
        save_users(data)

def get_pending_jobs(username):
    """جلب المهام المعلقة للمستخدم"""
    data = load_users()
    if username in data["users"]:
        return data["users"][username].get("pending_jobs", {})
    return {}

def remove_pending_job(username, job_id):
    """حذف مهمة معلقة بعد اكتمالها"""
    data = load_users()
    if username in data["users"] and "pending_jobs" in data["users"][username]:
        if job_id in data["users"][username]["pending_jobs"]:
            del data["users"][username]["pending_jobs"][job_id]
            save_users(data)

# ============ الإعدادات ============

BASE_API = "https://laws-gateway.moj.gov.sa/apis/legislations/v1/Judgements/get-details"

def build_search_url(filters, page):
    """بناء رابط البحث ديناميكياً حسب فلاتر المستخدم"""
    from urllib.parse import quote
    base = "https://laws.moj.gov.sa/ar/JudicialDecisionsList/2"
    params = [f"pageNumber={page}", "pageSize=12", "viewType=grid"]
    
    court_type = filters.get("courtType", "")
    if court_type:
        params.append(f"courtTypes={court_type}")
    
    court_id = filters.get("courtId", "")
    if court_id:
        params.append(f"courtId={court_id}")
    
    city = filters.get("city", "")
    if city:
        params.append(f"cityId={city}")
    
    term = filters.get("term", "")
    if term:
        params.append(f"term={quote(term)}")
    
    date_from = filters.get("dateFrom", "")
    if date_from:
        params.append(f"dateFrom={date_from}")
    
    date_to = filters.get("dateTo", "")
    if date_to:
        params.append(f"dateTo={date_to}")
    
    sorting = filters.get("sortingBy", "2")
    params.append(f"sortingBy={sorting}")
    
    judgment_number = filters.get("judgmentNumber", "")
    if judgment_number:
        params.append(f"judgmentNumber={quote(judgment_number)}")
    
    return base + "?" + "&".join(params)

API_HEADERS = {
    "Accept": "application/json, text/plain, */*",
    "Origin": "https://laws.moj.gov.sa",
    "Referer": "https://laws.moj.gov.sa/",
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "languageCode": "ar",
    "token": "token"
}

MOJ_HEADERS = {
    "Accept": "application/json, text/plain, */*",
    "Origin": "https://www.moj.gov.sa",
    "Referer": "https://www.moj.gov.sa/",
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
}

DATA_DIR = os.path.dirname(os.path.abspath(__file__))

# ============ إعدادات الأداء (نصف القوة - للحفاظ على باقي الخدمات) ============
MAX_WORKERS = 8              # نصف الكورات فقط
MAX_CONCURRENT_SESSIONS = 10  # نصف الجلسات المتزامنة
REQUEST_TIMEOUT = 45         # timeout أطول للاستقرار
DELAY_BETWEEN_REQUESTS = 0.2  # تأخير معتدل
BATCH_SIZE = 50              # حفظ كل 50 قضية

# ============ إدارة الجلسات المتعددة ============

sessions_lock = threading.Lock()
active_sessions = {}  # session_id -> session_state

def create_session():
    """إنشاء جلسة جديدة"""
    session_id = str(uuid.uuid4())[:8]
    with sessions_lock:
        if len(active_sessions) >= MAX_CONCURRENT_SESSIONS:
            # حذف أقدم جلسة منتهية
            for sid in list(active_sessions.keys()):
                if not active_sessions[sid]["running"]:
                    del active_sessions[sid]
                    break
        
        active_sessions[session_id] = {
            "running": False,
            "phase": "",
            "ids_total": 0,
            "ids_done": 0,
            "cases_total": 0,
            "cases_done": 0,
            "target": 0,
            "log": [],
            "output_file": "",
            "error": None,
            "finished": False,
            "log_queue": queue.Queue(),
            "created_at": time.time()
        }
    return session_id

def get_session(session_id):
    """جلب جلسة بالمعرّف"""
    with sessions_lock:
        return active_sessions.get(session_id)

def add_log(session_id, msg):
    """إضافة رسالة للسجل"""
    state = get_session(session_id)
    if state:
        state["log"].append(msg)
        if len(state["log"]) > 100:
            state["log"] = state["log"][-100:]
        state["log_queue"].put(msg)

# ============ HTTP Session مع Connection Pool ============

def create_http_session():
    """إنشاء HTTP session مع إعادة المحاولة و Connection Pool كبير"""
    sess = http_requests.Session()
    retry = Retry(
        total=5,
        backoff_factor=0.5,
        status_forcelist=[500, 502, 503, 504, 429],
        method_whitelist=["GET"]  # للتوافق مع Python 3.7
    )
    adapter = HTTPAdapter(
        max_retries=retry,
        pool_connections=15,
        pool_maxsize=15
    )
    sess.mount("https://", adapter)
    sess.mount("http://", adapter)
    return sess

# ============ دوال السحب ============

def clean_html(html_text):
    if not html_text:
        return None
    soup = BeautifulSoup(html_text, "html.parser")
    return soup.get_text(separator="\n").strip()

def fetch_case(http_session, case_id):
    """سحب تفاصيل قضية واحدة"""
    params = {"id": case_id, "lang": "ar", "IdentityNumber": "1124499656"}
    r = http_session.get(BASE_API, headers=API_HEADERS, params=params, timeout=REQUEST_TIMEOUT)
    r.raise_for_status()
    model = r.json().get("model", {})
    return {
        "id": case_id,
        "judgmentNumber": model.get("judgmentNumber"),
        "court": model.get("judgmentCourtName"),
        "city": model.get("judgmentCityName"),
        "hijriDate": model.get("judgmentHijriDate"),
        "judgmentText": clean_html(model.get("judgmentTextofRulling")),
        "appealText": clean_html(model.get("appealTextofRulling")),
    }

def fetch_case_wrapper(args):
    """Wrapper للسحب المتوازي - مع إعادة المحاولة"""
    http_session, case_id, index = args
    max_retries = 3
    
    for attempt in range(max_retries):
        try:
            result = fetch_case(http_session, case_id)
            return (index, result, None)
        except Exception as e:
            if attempt < max_retries - 1:
                time.sleep(1 * (attempt + 1))  # تأخير تصاعدي
                continue
            return (index, None, str(e))

def run_resume_scraper(session_id, job_id, username):
    """إكمال سحب القضايا الفاشلة"""
    state = get_session(session_id)
    if not state:
        return
    
    try:
        state["running"] = True
        
        # جلب المعرّفات الفاشلة
        pending = get_pending_jobs(username)
        if job_id not in pending:
            add_log(session_id, "❌ المهمة غير موجودة")
            state["error"] = "المهمة غير موجودة"
            return
        
        job = pending[job_id]
        case_ids = job["failed_ids"]
        output_file = job["output_file"]
        
        # تحميل البيانات الموجودة
        existing_cases = []
        if os.path.exists(output_file):
            with open(output_file, "r", encoding="utf-8") as f:
                existing_cases = json.load(f)
        
        state["phase"] = "resume"
        state["cases_total"] = len(case_ids)
        state["target"] = len(case_ids)
        state["output_file"] = output_file
        state["username"] = username
        
        add_log(session_id, f"🔄 إكمال سحب {len(case_ids)} قضية فاشلة...")
        add_log(session_id, f"📁 الملف: {os.path.basename(output_file)}")
        
        all_cases = [None] * len(case_ids)
        completed = 0
        errors = 0
        new_failed_ids = []
        
        http_sess = create_http_session()
        tasks = [(http_sess, cid, i) for i, cid in enumerate(case_ids)]
        
        with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
            futures = {executor.submit(fetch_case_wrapper, task): task for task in tasks}
            
            for future in as_completed(futures):
                if not state["running"]:
                    add_log(session_id, "⏹️ تم إيقاف السحب")
                    break
                
                index, result, error = future.result()
                
                if result:
                    all_cases[index] = result
                    completed += 1
                    state["cases_done"] = completed
                    
                    if completed % 20 == 0:
                        add_log(session_id, f"📥 [{completed}/{len(case_ids)}]")
                else:
                    errors += 1
                    new_failed_ids.append(case_ids[index])
                
                time.sleep(DELAY_BETWEEN_REQUESTS)
        
        # دمج مع البيانات الموجودة
        new_cases = [c for c in all_cases if c]
        all_combined = existing_cases + new_cases
        
        with open(output_file, "w", encoding="utf-8") as f:
            json.dump(all_combined, f, ensure_ascii=False, indent=2)
        
        # تحديث المعرّفات الفاشلة
        if new_failed_ids:
            save_failed_ids(username, job_id, new_failed_ids, output_file)
            add_log(session_id, f"📋 تبقى {len(new_failed_ids)} قضية فاشلة")
        else:
            # حذف المهمة المعلقة لأنها اكتملت
            remove_pending_job(username, job_id)
            add_log(session_id, "✅ تم إكمال جميع القضايا!")
        
        state["finished"] = True
        add_log(session_id, f"🎉 تم إضافة {len(new_cases)} قضية (المجموع: {len(all_combined)})")
        
    except Exception as e:
        state["error"] = str(e)
        add_log(session_id, f"💥 خطأ: {e}")
    finally:
        state["running"] = False

def run_scraper(session_id, target_count, filters=None):
    """ثريد السحب الرئيسي - محسّن للسرعة"""
    state = get_session(session_id)
    if not state:
        return
    
    if filters is None:
        filters = {"courtType": "2", "courtId": "3", "term": "المحكمة التجارية", "dateFrom": "1443-01-01", "dateTo": "1446-12-29", "sortingBy": "2"}
    
    try:
        state["running"] = True
        state["target"] = target_count
        state["filters"] = filters
        timestamp = int(time.time())
        output_file = os.path.join(DATA_DIR, f"cases_{session_id}_{target_count}.json")
        ids_file = os.path.join(DATA_DIR, f"ids_{session_id}.json")
        state["output_file"] = output_file

        # ──── المرحلة 1: سحب المعرّفات ────
        state["phase"] = "ids"
        add_log(session_id, f"🚀 بدء سحب {target_count} قضية...")
        
        filter_desc = []
        if filters.get("courtId"):
            court_names = {"1": "العامة", "2": "الجزائية", "3": "التجارية", "4": "العمالية", "5": "الأحوال الشخصية", "9": "العليا"}
            filter_desc.append(f"المحكمة: {court_names.get(filters['courtId'], filters['courtId'])}")
        if filters.get("term"):
            filter_desc.append(f"بحث: {filters['term']}")
        if filters.get("dateFrom"):
            filter_desc.append(f"من: {filters['dateFrom']}")
        if filters.get("dateTo"):
            filter_desc.append(f"إلى: {filters['dateTo']}")
        if filter_desc:
            add_log(session_id, f"🔍 الفلاتر: {' | '.join(filter_desc)}")
        
        add_log(session_id, "📌 المرحلة 1: جمع معرّفات القضايا")

        all_ids = []
        id_set = set()
        consecutive_empty = 0
        max_pages = (target_count // 12) + 15

        with sync_playwright() as p:
            browser = p.chromium.launch(
                headless=True,
                args=[
                    '--disable-dev-shm-usage',
                    '--no-sandbox',
                    '--disable-setuid-sandbox',
                    '--disable-gpu'
                ]
            )
            context = browser.new_context(
                viewport={'width': 1280, 'height': 720},
                user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
            )
            page = context.new_page()

            for pg in range(1, max_pages + 1):
                if not state["running"]:
                    add_log(session_id, "⏹️ تم إيقاف السحب")
                    browser.close()
                    return

                if len(all_ids) >= target_count:
                    add_log(session_id, f"🎯 تم جمع {len(all_ids)} معرّف!")
                    break

                url = build_search_url(filters, pg)
                state["ids_done"] = len(all_ids)
                state["ids_total"] = target_count

                try:
                    page.goto(url, timeout=30000)
                    try:
                        page.wait_for_selector(
                            "a[href*='/JudicialDecisionsList/2/']", timeout=12000
                        )
                    except:
                        consecutive_empty += 1
                        if consecutive_empty >= 3:
                            add_log(session_id, "🛑 لا مزيد من النتائج")
                            break
                        time.sleep(1)
                        continue

                    time.sleep(0.5)
                    links = page.query_selector_all("a[href*='/JudicialDecisionsList/2/']")
                    new_count = 0
                    for link in links:
                        href = link.get_attribute("href") or ""
                        cid = href.split("/")[-1]
                        if cid and cid not in id_set:
                            all_ids.append(cid)
                            id_set.add(cid)
                            new_count += 1

                    if new_count > 0:
                        consecutive_empty = 0
                    else:
                        consecutive_empty += 1
                        if consecutive_empty >= 3:
                            add_log(session_id, "🛑 لا مزيد من النتائج")
                            break

                    if pg % 5 == 0 or new_count > 0:
                        add_log(session_id, f"📄 صفحة {pg} → +{new_count} (المجموع: {len(all_ids)})")

                    state["ids_done"] = len(all_ids)
                    time.sleep(1)

                except Exception as e:
                    add_log(session_id, f"❌ خطأ صفحة {pg}: {str(e)[:60]}")
                    time.sleep(2)

            browser.close()

        # حفظ المعرّفات
        with open(ids_file, "w", encoding="utf-8") as f:
            json.dump(all_ids, f, ensure_ascii=False)

        case_ids = all_ids[:target_count]
        add_log(session_id, f"✅ تم جمع {len(case_ids)} معرّف")

        # ──── المرحلة 2: سحب التفاصيل (متوازي) ────
        state["phase"] = "details"
        state["cases_total"] = len(case_ids)
        add_log(session_id, f"📌 المرحلة 2: سحب تفاصيل {len(case_ids)} قضية")
        add_log(session_id, f"⚡ وضع السرعة: {MAX_WORKERS} ثريدات متوازية")

        all_cases = [None] * len(case_ids)
        completed = 0
        errors = 0
        failed_ids = []  # قائمة المعرّفات الفاشلة
        
        # إنشاء HTTP session مع connection pool
        http_sess = create_http_session()
        
        # تحضير المهام
        tasks = [(http_sess, cid, i) for i, cid in enumerate(case_ids)]
        
        # معالجة متوازية
        with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
            futures = {executor.submit(fetch_case_wrapper, task): task for task in tasks}
            
            for future in as_completed(futures):
                if not state["running"]:
                    add_log(session_id, "⏹️ تم إيقاف السحب")
                    break
                
                index, result, error = future.result()
                
                if result:
                    all_cases[index] = result
                    completed += 1
                    state["cases_done"] = completed
                    
                    if completed % BATCH_SIZE == 0:
                        num = result.get("judgmentNumber", "؟")
                        add_log(session_id, f"📥 [{completed}/{len(case_ids)}] آخر: {num}")
                        # حفظ مؤقت
                        valid_cases = [c for c in all_cases if c]
                        with open(output_file, "w", encoding="utf-8") as f:
                            json.dump(valid_cases, f, ensure_ascii=False, indent=2)
                else:
                    errors += 1
                    failed_ids.append(case_ids[index])  # حفظ المعرّف الفاشل
                    if errors <= 10:
                        add_log(session_id, f"❌ خطأ: {error[:40] if error else 'unknown'}")
                
                time.sleep(DELAY_BETWEEN_REQUESTS)

        # حفظ نهائي
        valid_cases = [c for c in all_cases if c]
        with open(output_file, "w", encoding="utf-8") as f:
            json.dump(valid_cases, f, ensure_ascii=False, indent=2)

        # إضافة الملف لقائمة المستخدم
        if state.get("username"):
            add_file_to_user(state["username"], os.path.basename(output_file))
            
            # حفظ المعرّفات الفاشلة إذا وجدت
            if failed_ids:
                save_failed_ids(state["username"], session_id, failed_ids, output_file)
                add_log(session_id, f"📋 تم حفظ {len(failed_ids)} معرّف فاشل للإكمال لاحقاً")

        state["finished"] = True
        add_log(session_id, f"🎉 تم! {len(valid_cases)} قضية محفوظة")
        if errors > 0:
            add_log(session_id, f"⚠️ {errors} أخطاء - يمكنك إكمال السحب لاحقاً")

    except Exception as e:
        state["error"] = str(e)
        add_log(session_id, f"💥 خطأ عام: {e}")
    finally:
        state["running"] = False


# ============ الراوتات ============

# ===== راوتات المصادقة =====

@app.route("/login")
def login_page():
    if 'user' in session:
        return redirect(url_for('index'))
    return render_template("login.html")

@app.route("/auth/login", methods=["POST"])
def auth_login():
    data = request.get_json()
    username = data.get("username", "").strip().lower()
    password = data.get("password", "")
    
    users = load_users()
    
    if username not in users["users"]:
        return jsonify({"error": "اسم المستخدم غير موجود"}), 401
    
    user = users["users"][username]
    # مقارنة كلمة المرور (مشفرة أو عادية للتوافق)
    if user["password"] == password or user["password"] == hash_password(password):
        session['user'] = username
        session['name'] = user.get("name", username)
        return jsonify({"success": True, "name": user.get("name", username)})
    
    return jsonify({"error": "كلمة المرور غير صحيحة"}), 401

@app.route("/auth/register", methods=["POST"])
def auth_register():
    data = request.get_json()
    username = data.get("username", "").strip().lower()
    name = data.get("name", "").strip()
    password = data.get("password", "")
    
    if len(username) < 3:
        return jsonify({"error": "اسم المستخدم يجب أن يكون 3 حروف على الأقل"}), 400
    
    if len(password) < 4:
        return jsonify({"error": "كلمة المرور يجب أن تكون 4 حروف على الأقل"}), 400
    
    users = load_users()
    
    if username in users["users"]:
        return jsonify({"error": "اسم المستخدم موجود مسبقاً"}), 400
    
    users["users"][username] = {
        "password": hash_password(password),
        "name": name or username,
        "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "files": []
    }
    save_users(users)
    
    return jsonify({"success": True})

@app.route("/auth/logout")
def auth_logout():
    session.pop('user', None)
    session.pop('name', None)
    return redirect(url_for('login_page'))

# ===== ACME Challenge for Let's Encrypt =====

@app.route("/.well-known/acme-challenge/<token>")
def acme_challenge(token):
    """Serve ACME challenge files for SSL certificate verification"""
    challenge_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".well-known", "acme-challenge", token)
    if os.path.exists(challenge_path):
        with open(challenge_path, 'r') as f:
            return Response(f.read(), mimetype='text/plain')
    return "Not Found", 404

# ===== الصفحة الرئيسية =====

@app.route("/")
@login_required
def index():
    # إنشاء جلسة جديدة لكل مستخدم
    session_id = create_session()
    session['session_id'] = session_id
    username = session.get('user', 'guest')
    name = session.get('name', username)
    return render_template("index.html", session_id=session_id, username=username, name=name)

@app.route("/start", methods=["POST"])
@login_required
def start_scraping():
    session_id = request.json.get("session_id") or session.get("session_id")
    username = session.get('user')
    
    if not session_id:
        session_id = create_session()
        session['session_id'] = session_id
    
    state = get_session(session_id)
    if not state:
        session_id = create_session()
        state = get_session(session_id)
    
    # ربط الجلسة بالمستخدم
    state["username"] = username
    
    if state["running"]:
        return jsonify({"error": "السحب يعمل بالفعل", "session_id": session_id}), 400

    data = request.get_json()
    count = int(data.get("count", 10))
    if count < 1 or count > 10000:
        return jsonify({"error": "العدد يجب أن يكون بين 1 و 10000"}), 400

    filters = {
        "courtType": str(data.get("courtType", "")),
        "courtId": str(data.get("courtId", "")),
        "city": str(data.get("city", "")),
        "term": str(data.get("term", "")),
        "dateFrom": str(data.get("dateFrom", "")),
        "dateTo": str(data.get("dateTo", "")),
        "sortingBy": str(data.get("sortingBy", "2")),
        "judgmentNumber": str(data.get("judgmentNumber", "")),
    }

    state.update({
        "running": False,
        "phase": "",
        "ids_total": 0,
        "ids_done": 0,
        "cases_total": 0,
        "cases_done": 0,
        "target": count,
        "log": [],
        "output_file": "",
        "error": None,
        "finished": False,
    })
    
    t = threading.Thread(target=run_scraper, args=(session_id, count, filters), daemon=True)
    t.start()

    return jsonify({"status": "started", "count": count, "session_id": session_id})

@app.route("/stop", methods=["POST"])
def stop_scraping():
    session_id = request.json.get("session_id") or session.get("session_id")
    state = get_session(session_id)
    if state:
        state["running"] = False
    return jsonify({"status": "stopped"})

@app.route("/status")
def get_status():
    session_id = request.args.get("session_id") or session.get("session_id")
    state = get_session(session_id)
    
    if not state:
        return jsonify({"error": "جلسة غير موجودة"}), 404
    
    return jsonify({
        "session_id": session_id,
        "running": state["running"],
        "phase": state["phase"],
        "ids_done": state["ids_done"],
        "ids_total": state["ids_total"],
        "cases_done": state["cases_done"],
        "cases_total": state["cases_total"],
        "target": state["target"],
        "finished": state["finished"],
        "error": state["error"],
        "log": state["log"][-30:],
    })

@app.route("/stream")
def stream():
    session_id = request.args.get("session_id") or session.get("session_id")
    state = get_session(session_id)
    
    if not state:
        return jsonify({"error": "جلسة غير موجودة"}), 404
    
    def generate():
        while True:
            try:
                msg = state["log_queue"].get(timeout=2)
                yield f"data: {json.dumps({'msg': msg}, ensure_ascii=False)}\n\n"
            except queue.Empty:
                yield f"data: {json.dumps({'heartbeat': True})}\n\n"
            if state["finished"] or state["error"]:
                yield f"data: {json.dumps({'done': True})}\n\n"
                break

    return Response(generate(), mimetype="text/event-stream")

@app.route("/download")
def download_file():
    session_id = request.args.get("session_id") or session.get("session_id")
    state = get_session(session_id)
    
    if not state:
        return jsonify({"error": "جلسة غير موجودة"}), 404
    
    filepath = state.get("output_file", "")
    if filepath and os.path.exists(filepath):
        return send_file(filepath, as_attachment=True, attachment_filename=os.path.basename(filepath))
    return jsonify({"error": "الملف غير موجود"}), 404

@app.route("/files")
@login_required
def list_files():
    """قائمة ملفات JSON الخاصة بالمستخدم"""
    username = session.get('user')
    user_files = get_user_files(username)
    
    files = []
    for f in os.listdir(DATA_DIR):
        if f.endswith(".json") and f.startswith("cases"):
            # عرض فقط ملفات المستخدم الحالي
            if f not in user_files:
                continue
            path = os.path.join(DATA_DIR, f)
            size = os.path.getsize(path)  # بالبايت
            try:
                with open(path, "r", encoding="utf-8") as fh:
                    data = json.load(fh)
                    count = len(data) if isinstance(data, list) else 0
            except Exception:
                count = 0
            files.append({"name": f, "size": size, "count": count})
    # ترتيب بالأحدث
    files.sort(key=lambda x: os.path.getmtime(os.path.join(DATA_DIR, x["name"])), reverse=True)
    return jsonify(files)

@app.route("/download/<filename>")
@login_required
def download_named(filename):
    username = session.get('user')
    user_files = get_user_files(username)
    
    # التحقق أن الملف يخص المستخدم
    if filename not in user_files:
        return jsonify({"error": "ليس لديك صلاحية لهذا الملف"}), 403
    
    filepath = os.path.join(DATA_DIR, filename)
    if os.path.exists(filepath) and filename.endswith(".json"):
        return send_file(filepath, as_attachment=True, attachment_filename=filename)
    return jsonify({"error": "الملف غير موجود"}), 404

@app.route("/sessions")
def list_sessions():
    """عرض الجلسات النشطة (للمراقبة)"""
    with sessions_lock:
        info = []
        for sid, state in active_sessions.items():
            info.append({
                "session_id": sid,
                "running": state["running"],
                "phase": state["phase"],
                "progress": f"{state['cases_done']}/{state['cases_total']}",
                "finished": state["finished"]
            })
    return jsonify({"active_sessions": len(info), "max_sessions": MAX_CONCURRENT_SESSIONS, "sessions": info})

@app.route("/pending")
@login_required
def list_pending():
    """عرض المهام المعلقة للمستخدم"""
    username = session.get('user')
    pending = get_pending_jobs(username)
    
    jobs = []
    for job_id, job in pending.items():
        jobs.append({
            "job_id": job_id,
            "count": job["count"],
            "output_file": os.path.basename(job["output_file"]),
            "created_at": job["created_at"]
        })
    
    return jsonify(jobs)

@app.route("/resume", methods=["POST"])
@login_required
def resume_scraping():
    """إكمال سحب القضايا الفاشلة"""
    data = request.get_json()
    job_id = data.get("job_id")
    username = session.get('user')
    
    if not job_id:
        return jsonify({"error": "معرّف المهمة مطلوب"}), 400
    
    pending = get_pending_jobs(username)
    if job_id not in pending:
        return jsonify({"error": "المهمة غير موجودة"}), 404
    
    # إنشاء جلسة جديدة
    session_id = create_session()
    session['session_id'] = session_id
    state = get_session(session_id)
    
    # بدء الإكمال
    t = threading.Thread(target=run_resume_scraper, args=(session_id, job_id, username), daemon=True)
    t.start()
    
    return jsonify({
        "status": "resuming",
        "session_id": session_id,
        "count": pending[job_id]["count"]
    })


# ============ صفحة التحليل ============

@app.route("/analytics")
@login_required
def analytics_page():
    """صفحة تحليل البيانات"""
    return render_template("analytics.html")

@app.route("/api/analyze/<filename>")
@login_required
def analyze_file(filename):
    """تحليل ملف القضايا وإرجاع إحصائيات شاملة"""
    from collections import Counter
    import re
    
    username = session.get('user')
    user_files = get_user_files(username)
    
    # التحقق من صلاحية الوصول للملف
    if filename not in user_files:
        return jsonify({"error": "لا تملك صلاحية الوصول لهذا الملف"}), 403
    
    filepath = os.path.join(DATA_DIR, filename)
    if not os.path.exists(filepath):
        return jsonify({"error": "الملف غير موجود"}), 404
    
    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            data = json.load(f)
    except Exception as e:
        return jsonify({"error": f"خطأ في قراءة الملف: {str(e)}"}), 500
    
    if not isinstance(data, list):
        return jsonify({"error": "صيغة الملف غير صحيحة"}), 400
    
    total_cases = len(data)
    
    # 1. إحصائيات أساسية
    judgment_numbers = [c.get('judgmentNumber') for c in data if c.get('judgmentNumber')]
    ids = [c.get('id') for c in data if c.get('id')]
    
    # التحقق من التكرار
    num_counts = Counter(judgment_numbers)
    duplicate_nums = {n: c for n, c in num_counts.items() if c > 1}
    
    id_counts = Counter(ids)
    duplicate_ids = {i: c for i, c in id_counts.items() if c > 1}
    
    # 2. توزيع المحاكم
    courts = [c.get('court') for c in data if c.get('court')]
    court_stats = Counter(courts).most_common()
    
    # 3. توزيع المدن
    cities = [c.get('city') for c in data if c.get('city')]
    city_stats = Counter(cities).most_common()
    
    # 4. تحليل النصوص
    judgment_texts = [c.get('judgmentText', '') for c in data if c.get('judgmentText')]
    appeal_texts = [c.get('appealText', '') for c in data if c.get('appealText')]
    
    cases_with_judgment = len(judgment_texts)
    cases_with_appeal = len(appeal_texts)
    
    # 5. حساب متوسط أطوال النصوص
    avg_judgment_length = sum(len(t) for t in judgment_texts) / len(judgment_texts) if judgment_texts else 0
    avg_appeal_length = sum(len(t) for t in appeal_texts) / len(appeal_texts) if appeal_texts else 0
    
    # 6. استخراج الكلمات المفتاحية الأكثر شيوعاً
    all_text = ' '.join(judgment_texts[:500])  # أول 500 قضية لتجنب البطء
    
    # كلمات قانونية مهمة للبحث عنها
    legal_keywords = {
        'فسخ العقد': 0,
        'التعويض': 0,
        'المطالبة': 0,
        'إلزام': 0,
        'رد الدعوى': 0,
        'عدم قبول': 0,
        'المدعي': 0,
        'المدعى عليه': 0,
        'الشركة': 0,
        'المؤسسة': 0,
        'العقد': 0,
        'الضمان': 0,
        'التأخير': 0,
        'الفسخ': 0,
        'البيع': 0,
        'الشراء': 0,
        'الأجرة': 0,
        'المبلغ': 0,
        'السداد': 0,
        'المماطلة': 0,
        'العلامة التجارية': 0,
        'الملكية الفكرية': 0,
        'الإفلاس': 0,
        'التصفية': 0,
        'الوكالة': 0,
        'الشراكة': 0
    }
    
    for keyword in legal_keywords:
        legal_keywords[keyword] = all_text.count(keyword)
    
    # ترتيب حسب التكرار
    sorted_keywords = sorted(legal_keywords.items(), key=lambda x: x[1], reverse=True)
    top_keywords = [{"keyword": k, "count": v} for k, v in sorted_keywords if v > 0][:15]
    
    # 7. تحليل المبالغ المالية
    money_pattern = r'(\d{1,3}(?:,\d{3})*(?:\.\d{2})?)\s*ريال'
    all_amounts = []
    for text in judgment_texts[:1000]:
        amounts = re.findall(money_pattern, text)
        for amt in amounts:
            try:
                num = float(amt.replace(',', ''))
                if num > 100:  # تجاهل المبالغ الصغيرة جداً
                    all_amounts.append(num)
            except:
                pass
    
    amount_stats = {}
    if all_amounts:
        all_amounts.sort()
        amount_stats = {
            "total_found": len(all_amounts),
            "min": min(all_amounts),
            "max": max(all_amounts),
            "avg": sum(all_amounts) / len(all_amounts),
            "median": all_amounts[len(all_amounts)//2],
            "ranges": {
                "أقل من 10,000": len([a for a in all_amounts if a < 10000]),
                "10,000 - 50,000": len([a for a in all_amounts if 10000 <= a < 50000]),
                "50,000 - 100,000": len([a for a in all_amounts if 50000 <= a < 100000]),
                "100,000 - 500,000": len([a for a in all_amounts if 100000 <= a < 500000]),
                "500,000 - 1,000,000": len([a for a in all_amounts if 500000 <= a < 1000000]),
                "أكثر من 1,000,000": len([a for a in all_amounts if a >= 1000000])
            }
        }
    
    # 8. تحليل الأنظمة المرجعية (أكثر الأنظمة تكراراً)
    law_references = {
        'نظام المحاكم التجارية': 0,
        'نظام الشركات': 0,
        'نظام الأوراق التجارية': 0,
        'نظام المرافعات الشرعية': 0,
        'نظام المرافعات': 0,
        'نظام التنفيذ': 0,
        'نظام العمل': 0,
        'نظام الإثبات': 0,
        'نظام التحكيم': 0,
        'نظام الإفلاس': 0,
        'نظام المعاملات المدنية': 0,
        'نظام المنافسة': 0,
        'نظام العلامات التجارية': 0,
        'نظام التجارة الإلكترونية': 0,
        'نظام المنافسات والمشتريات الحكومية': 0,
        'نظام الإجراءات الجزائية': 0,
        'نظام التأمين التعاوني': 0,
        'نظام الامتياز التجاري': 0,
        'نظام السجل التجاري': 0,
        'نظام الأسماء التجارية': 0,
        'نظام الرهن التجاري': 0,
        'نظام مكافحة الغش التجاري': 0,
        'نظام حماية المستهلك': 0,
        'نظام التمويل': 0,
        'نظام الملكية الفكرية': 0,
        'نظام براءات الاختراع': 0,
        'نظام المحاماة': 0,
        'نظام التكاليف القضائية': 0,
        'نظام القضاء': 0,
        'نظام التستر التجاري': 0,
    }
    
    all_texts_for_laws = judgment_texts + appeal_texts
    for text in all_texts_for_laws:
        for law_name in law_references:
            law_references[law_name] += text.count(law_name)
    
    # دمج "نظام المرافعات" مع "نظام المرافعات الشرعية" لتجنب التكرار
    law_references['نظام المرافعات الشرعية'] += law_references.pop('نظام المرافعات', 0)
    
    sorted_laws = sorted(law_references.items(), key=lambda x: x[1], reverse=True)
    top_laws = [{"law": name, "count": count} for name, count in sorted_laws if count > 0]
    
    # 9. تحليل نتائج الأحكام
    verdict_keywords = {
        "قبول الدعوى": 0,
        "رد الدعوى": 0,
        "رفض الدعوى": 0,
        "عدم قبول الدعوى": 0,
        "إلزام المدعى عليه": 0,
        "تأييد الحكم": 0,
        "نقض الحكم": 0,
        "إلغاء الحكم": 0
    }
    
    for text in judgment_texts:
        for keyword in verdict_keywords:
            if keyword in text:
                verdict_keywords[keyword] += 1
    
    verdict_stats = [{"verdict": k, "count": v} for k, v in sorted(verdict_keywords.items(), key=lambda x: x[1], reverse=True) if v > 0]
    
    # 9. القضايا ذات الاستئناف
    cases_appealed = len([c for c in data if c.get('appealText')])
    appeal_rate = (cases_appealed / total_cases * 100) if total_cases > 0 else 0
    
    # 10. أطول وأقصر الأحكام
    texts_with_length = [(i, len(c.get('judgmentText', ''))) for i, c in enumerate(data) if c.get('judgmentText')]
    texts_with_length.sort(key=lambda x: x[1], reverse=True)
    
    longest_cases = []
    for idx, length in texts_with_length[:5]:
        case = data[idx]
        longest_cases.append({
            "judgmentNumber": case.get('judgmentNumber', 'غير معروف'),
            "court": case.get('court', 'غير معروف'),
            "city": case.get('city', 'غير معروف'),
            "length": length
        })
    
    shortest_cases = []
    for idx, length in texts_with_length[-5:]:
        case = data[idx]
        shortest_cases.append({
            "judgmentNumber": case.get('judgmentNumber', 'غير معروف'),
            "court": case.get('court', 'غير معروف'),
            "city": case.get('city', 'غير معروف'),
            "length": length
        })
    
    # إعداد الاستجابة
    result = {
        "filename": filename,
        "summary": {
            "total_cases": total_cases,
            "unique_judgment_numbers": len(set(judgment_numbers)),
            "duplicate_judgment_numbers": len(duplicate_nums),
            "duplicate_ids": len(duplicate_ids),
            "cases_with_judgment_text": cases_with_judgment,
            "cases_with_appeal_text": cases_with_appeal,
            "appeal_rate_percent": round(appeal_rate, 2)
        },
        "duplicates": {
            "judgment_numbers": [{"number": n, "count": c} for n, c in sorted(duplicate_nums.items(), key=lambda x: x[1], reverse=True)[:20]],
            "total_duplicate_numbers": len(duplicate_nums)
        },
        "court_distribution": [{"court": c, "count": n} for c, n in court_stats],
        "city_distribution": [{"city": c, "count": n} for c, n in city_stats],
        "text_analysis": {
            "avg_judgment_length": round(avg_judgment_length),
            "avg_appeal_length": round(avg_appeal_length),
            "top_keywords": top_keywords,
            "verdict_stats": verdict_stats,
            "top_laws": top_laws
        },
        "financial_analysis": amount_stats,
        "case_lengths": {
            "longest": longest_cases,
            "shortest": shortest_cases
        }
    }
    
    return jsonify(result)


# ============ صفحة تصفح القضايا ============

@app.route("/browse")
@login_required
def browse_page():
    """صفحة تصفح القضايا"""
    return render_template("browse.html")

@app.route("/cleanup")
@login_required
def cleanup_page():
    """صفحة تنظيف البيانات"""
    return render_template("cleanup.html")

@app.route("/api/cases/<filename>")
@login_required
def get_cases(filename):
    """جلب القضايا من ملف للتصفح"""
    username = session.get('user')
    user_files = get_user_files(username)
    
    # التحقق من صلاحية الوصول للملف
    if filename not in user_files:
        return jsonify({"error": "لا تملك صلاحية الوصول لهذا الملف"}), 403
    
    filepath = os.path.join(DATA_DIR, filename)
    if not os.path.exists(filepath):
        return jsonify({"error": "الملف غير موجود"}), 404
    
    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            data = json.load(f)
    except Exception as e:
        return jsonify({"error": f"خطأ في قراءة الملف: {str(e)}"}), 500
    
    if not isinstance(data, list):
        return jsonify({"error": "صيغة الملف غير صحيحة"}), 400
    
    return jsonify({"cases": data, "total": len(data)})

@app.route("/api/export-csv/<filename>")
@login_required
def export_csv(filename):
    """تصدير القضايا كملف CSV"""
    import csv
    import io
    
    username = session.get('user')
    user_files = get_user_files(username)
    
    if filename not in user_files:
        return jsonify({"error": "لا تملك صلاحية الوصول لهذا الملف"}), 403
    
    filepath = os.path.join(DATA_DIR, filename)
    if not os.path.exists(filepath):
        return jsonify({"error": "الملف غير موجود"}), 404
    
    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            data = json.load(f)
        
        if not isinstance(data, list) or len(data) == 0:
            return jsonify({"error": "لا توجد بيانات للتصدير"}), 400
        
        # إنشاء CSV
        output = io.StringIO()
        
        # الأعمدة
        fieldnames = ['id', 'judgmentNumber', 'court', 'city', 'hijriDate', 'judgmentText', 'appealText']
        writer = csv.DictWriter(output, fieldnames=fieldnames, extrasaction='ignore')
        
        writer.writeheader()
        for case in data:
            # تنظيف النصوص الطويلة
            row = {
                'id': case.get('id', ''),
                'judgmentNumber': case.get('judgmentNumber', ''),
                'court': case.get('court', ''),
                'city': case.get('city', ''),
                'hijriDate': case.get('hijriDate', ''),
                'judgmentText': (case.get('judgmentText', '') or '')[:500] + '...' if len(case.get('judgmentText', '') or '') > 500 else case.get('judgmentText', ''),
                'appealText': (case.get('appealText', '') or '')[:500] + '...' if len(case.get('appealText', '') or '') > 500 else case.get('appealText', '')
            }
            writer.writerow(row)
        
        # إعداد الاستجابة
        output.seek(0)
        csv_content = output.getvalue()
        
        # إضافة BOM للعربية
        response = Response(
            '\ufeff' + csv_content,
            mimetype='text/csv; charset=utf-8',
            headers={
                'Content-Disposition': f'attachment; filename={filename.replace(".json", ".csv")}'
            }
        )
        return response
        
    except Exception as e:
        return jsonify({"error": f"خطأ في التصدير: {str(e)}"}), 500

@app.route("/api/case/<filename>/<int:index>")
@login_required
def get_single_case(filename, index):
    """جلب قضية واحدة بالتفصيل"""
    username = session.get('user')
    user_files = get_user_files(username)
    
    if filename not in user_files:
        return jsonify({"error": "لا تملك صلاحية الوصول لهذا الملف"}), 403
    
    filepath = os.path.join(DATA_DIR, filename)
    if not os.path.exists(filepath):
        return jsonify({"error": "الملف غير موجود"}), 404
    
    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            data = json.load(f)
        
        if index < 0 or index >= len(data):
            return jsonify({"error": "رقم القضية غير صالح"}), 400
        
        return jsonify(data[index])
    except Exception as e:
        return jsonify({"error": f"خطأ: {str(e)}"}), 500


# ============ API تحليل متقدم ============

@app.route("/api/advanced-analysis/<filename>")
@login_required
def advanced_analysis(filename):
    """تحليل متقدم مع مزيد من الإحصائيات"""
    from collections import Counter
    import re
    from datetime import datetime
    
    username = session.get('user')
    user_files = get_user_files(username)
    
    if filename not in user_files:
        return jsonify({"error": "لا تملك صلاحية الوصول لهذا الملف"}), 403
    
    filepath = os.path.join(DATA_DIR, filename)
    if not os.path.exists(filepath):
        return jsonify({"error": "الملف غير موجود"}), 404
    
    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            data = json.load(f)
    except Exception as e:
        return jsonify({"error": f"خطأ في قراءة الملف: {str(e)}"}), 500
    
    if not isinstance(data, list):
        return jsonify({"error": "صيغة الملف غير صحيحة"}), 400
    
    total_cases = len(data)
    
    # تحليل التواريخ الهجرية
    hijri_dates = [c.get('hijriDate', '') for c in data if c.get('hijriDate')]
    year_pattern = r'(\d{4})'
    years = []
    for date in hijri_dates:
        match = re.search(year_pattern, date)
        if match:
            years.append(match.group(1))
    year_stats = Counter(years).most_common()
    
    # تحليل شهري
    month_pattern = r'/(\d{1,2})/'
    months = []
    for date in hijri_dates:
        match = re.search(month_pattern, date)
        if match:
            months.append(int(match.group(1)))
    
    month_names = {
        1: 'محرم', 2: 'صفر', 3: 'ربيع الأول', 4: 'ربيع الثاني',
        5: 'جمادى الأولى', 6: 'جمادى الآخرة', 7: 'رجب', 8: 'شعبان',
        9: 'رمضان', 10: 'شوال', 11: 'ذو القعدة', 12: 'ذو الحجة'
    }
    month_stats = Counter(months).most_common()
    month_stats = [{"month": month_names.get(m, str(m)), "count": c} for m, c in month_stats]
    
    # تحليل أنواع القضايا من العناوين
    judgment_texts = [c.get('judgmentText', '') for c in data if c.get('judgmentText')]
    
    case_types = {
        'عقود تجارية': ['عقد', 'اتفاقية', 'مقاولة', 'توريد', 'إيجار'],
        'تعويضات': ['تعويض', 'أضرار', 'خسائر', 'ضرر'],
        'شركات': ['شركة', 'مساهمة', 'شراكة', 'تصفية', 'إفلاس'],
        'ملكية فكرية': ['علامة تجارية', 'براءة اختراع', 'حقوق', 'ملكية فكرية'],
        'أوراق تجارية': ['كمبيالة', 'شيك', 'سند', 'أمر دفع'],
        'وكالات': ['وكالة', 'توزيع', 'وكيل'],
        'تأمين': ['تأمين', 'وثيقة', 'مطالبة تأمينية'],
        'بنوك': ['بنك', 'قرض', 'تمويل', 'رهن']
    }
    
    type_counts = {k: 0 for k in case_types.keys()}
    for text in judgment_texts[:1000]:  # أول 1000 قضية
        text_lower = text
        for case_type, keywords in case_types.items():
            for keyword in keywords:
                if keyword in text_lower:
                    type_counts[case_type] += 1
                    break
    
    type_distribution = [{"type": k, "count": v} for k, v in sorted(type_counts.items(), key=lambda x: x[1], reverse=True) if v > 0]
    
    # تحليل أطوال النصوص
    text_lengths = [len(c.get('judgmentText', '')) for c in data if c.get('judgmentText')]
    length_ranges = {
        'قصيرة (< 2000)': len([l for l in text_lengths if l < 2000]),
        'متوسطة (2000-5000)': len([l for l in text_lengths if 2000 <= l < 5000]),
        'طويلة (5000-10000)': len([l for l in text_lengths if 5000 <= l < 10000]),
        'طويلة جداً (> 10000)': len([l for l in text_lengths if l >= 10000])
    }
    
    # تحليل المحاكم حسب المدينة
    court_city = {}
    for c in data:
        city = c.get('city', 'غير معروف')
        court = c.get('court', 'غير معروف')
        if city not in court_city:
            court_city[city] = {}
        if court not in court_city[city]:
            court_city[city][court] = 0
        court_city[city][court] += 1
    
    # تحليل نسبة الاستئناف حسب المدينة
    appeal_by_city = {}
    for c in data:
        city = c.get('city', 'غير معروف')
        if city not in appeal_by_city:
            appeal_by_city[city] = {'total': 0, 'appealed': 0}
        appeal_by_city[city]['total'] += 1
        if c.get('appealText'):
            appeal_by_city[city]['appealed'] += 1
    
    appeal_rates = []
    for city, stats in appeal_by_city.items():
        if stats['total'] >= 10:  # فقط المدن بـ 10 قضايا على الأقل
            rate = (stats['appealed'] / stats['total']) * 100
            appeal_rates.append({
                'city': city,
                'total': stats['total'],
                'appealed': stats['appealed'],
                'rate': round(rate, 1)
            })
    appeal_rates.sort(key=lambda x: x['rate'], reverse=True)
    
    # تحليل الكلمات الأكثر تكراراً في منطوق الحكم
    verdict_section_pattern = r'(حكمت المحكمة|فإن المحكمة تحكم|منطوق الحكم)[^\.]*\.'
    verdict_words = []
    for text in judgment_texts[:500]:
        matches = re.findall(verdict_section_pattern, text)
        for match in matches:
            words = re.findall(r'[\u0600-\u06FF]+', match)
            verdict_words.extend(words)
    
    # إزالة الكلمات الشائعة
    stop_words = {'في', 'من', 'إلى', 'على', 'عن', 'مع', 'هذا', 'هذه', 'التي', 'الذي', 'أن', 'ان', 'و', 'أو', 'لم', 'لا', 'ما', 'هو', 'هي', 'كان', 'كانت', 'قد', 'قبل', 'بعد', 'حتى', 'إذا', 'اذا', 'عند', 'بها', 'به', 'لها', 'له', 'منها', 'منه', 'فيها', 'فيه', 'المحكمة', 'حكمت', 'تحكم', 'الحكم', 'منطوق'}
    verdict_words = [w for w in verdict_words if len(w) > 2 and w not in stop_words]
    verdict_word_freq = Counter(verdict_words).most_common(20)
    
    result = {
        "filename": filename,
        "total_cases": total_cases,
        "date_analysis": {
            "by_year": [{"year": y, "count": c} for y, c in year_stats],
            "by_month": month_stats
        },
        "case_type_distribution": type_distribution,
        "text_length_distribution": [{"range": k, "count": v} for k, v in length_ranges.items()],
        "appeal_by_city": appeal_rates[:10],
        "court_by_city": court_city,
        "verdict_keywords": [{"word": w, "count": c} for w, c in verdict_word_freq]
    }
    
    return jsonify(result)


# ============ الأنظمة السعودية - NoSQL (ملف واحد) ============

LAWS_DB_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "laws_db.json")
_laws_db_cache = None
_laws_db_lock = threading.Lock()

AVAILABLE_LAWS = {
    "38334008-3b70-4c6c-b3af-aba3016a8061": "نظام المحاكم التجارية",
    "f0eaae46-9f84-40ee-815e-a9a700f268b3": "نظام المرافعات الشرعية",
    "a8376aea-1bc3-49d4-9027-aed900b555af": "نظام الشركات",
    "2716057c-c097-4bad-8e1e-ae1400c678d5": "نظام الإثبات",
    "3ec4414f-2ec5-48b1-bcb4-a9a700f1aa2b": "نظام العلامات التجارية",
    "655fdb42-8c96-422b-b8c4-b04f0095c94c": "نظام المعاملات المدنية",
    "f42655be-79b0-4fd4-bb90-a9a700f26a3e": "نظام المحاماة",
    "68204119-84f1-4789-8fad-a9ec014c3788": "نظام الإفلاس",
    "5535039e-13da-43f6-8f53-a9a700f26485": "نظام التحكيم",
    "d4801a45-7f76-4414-bdca-b20900db6fc4": "نظام الأسماء التجارية",
    "af2a6b93-51dd-4f16-b781-aafd00d9fbbc": "نظام الامتياز التجاري",
    "c81ba2f1-1bf1-443b-9b1c-a9a700f27110": "نظام التنفيذ",
    "c2c05ee1-201a-48de-91e7-a9a700f2d14f": "نظام المنافسات والمشتريات الحكومية",
    "ea1765a3-dec3-41a0-a32f-a9a700f26d58": "نظام القضاء",
    "08381293-6388-48e2-8ad2-a9a700f2aa94": "نظام العمل",
    "4763eb94-047b-46f1-9697-a9a700f1b7ed": "نظام الأوراق التجارية",
    "3e368087-7b31-46e7-8005-ada100b8f703": "نظام التكاليف القضائية",
    "f95138ff-0892-49b0-921c-a9a700f1c37f": "نظام براءات الاختراع",
    "8f1b7079-a5f0-425d-b5e0-a9a700f26b2d": "نظام الإجراءات الجزائية",
    "6b615a05-f637-41cf-8419-a9a700f1afd7": "نظام المنافسة",
    "98ee4b51-d398-4323-ae69-b2b8009f3156": "نظام السجل التجاري",
    "85eb2897-bec6-4c0c-b1d7-ac7c008ec09c": "نظام مكافحة الغش التجاري",
    "bf9e0aae-6df6-4785-a305-ac2300bd0856": "نظام مكافحة التستر",
    "360de590-0286-4fa5-a243-aa9100c31979": "نظام التجارة الإلكترونية",
    "f53c97f5-d253-4828-89d4-a9a700f2d829": "نظام مراقبة شركات التمويل",
}

def load_laws_db():
    """تحميل قاعدة بيانات الأنظمة (مع كاش في الذاكرة)"""
    global _laws_db_cache
    if _laws_db_cache is not None:
        return _laws_db_cache
    with _laws_db_lock:
        if _laws_db_cache is not None:
            return _laws_db_cache
        if os.path.exists(LAWS_DB_FILE):
            with open(LAWS_DB_FILE, 'r', encoding='utf-8') as f:
                _laws_db_cache = json.load(f)
        else:
            _laws_db_cache = {"_meta": {"version": 1, "totalLaws": 0, "totalArticles": 0}, "laws": {}}
        return _laws_db_cache

def save_laws_db(db=None):
    """حفظ قاعدة بيانات الأنظمة"""
    global _laws_db_cache
    with _laws_db_lock:
        if db is not None:
            _laws_db_cache = db
        if _laws_db_cache is None:
            return
        laws = _laws_db_cache.get("laws", {})
        total = len(laws)
        total_articles = sum(l.get("articlesCount", 0) for l in laws.values())
        index = {l.get("lawName", lid): {"id": lid, "articles": l.get("articlesCount", 0)} for lid, l in laws.items()}
        _laws_db_cache["_meta"] = {
            "version": 1,
            "totalLaws": total,
            "totalArticles": total_articles,
            "index": index
        }
        with open(LAWS_DB_FILE, 'w', encoding='utf-8') as f:
            json.dump(_laws_db_cache, f, ensure_ascii=False, indent=2)

def laws_db_get(law_id):
    """جلب نظام واحد من القاعدة"""
    db = load_laws_db()
    return db["laws"].get(law_id)

def laws_db_upsert(law_id, law_data):
    """إضافة أو تحديث نظام في القاعدة"""
    db = load_laws_db()
    db["laws"][law_id] = law_data
    save_laws_db(db)

def laws_db_list_ids():
    """قائمة الأيديات الموجودة"""
    db = load_laws_db()
    return set(db["laws"].keys())

@app.route("/laws")
@login_required
def laws_page():
    return render_template("laws.html", username=session.get('user'))

@app.route("/import_laws")
@login_required
def import_laws_page():
    return render_template("import_laws.html", username=session.get('user'))

@app.route("/api/laws/list")
@login_required
def list_available_laws():
    """قائمة كل الأنظمة المتاحة مع حالة التحميل"""
    db = load_laws_db()
    downloaded = db.get("laws", {})
    
    laws_list = []
    for law_id, law_name in AVAILABLE_LAWS.items():
        is_downloaded = law_id in downloaded
        laws_list.append({
            'lawId': law_id,
            'lawName': downloaded[law_id]['lawName'] if is_downloaded else law_name,
            'isDownloaded': is_downloaded,
            'articlesCount': downloaded[law_id].get('articlesCount', 0) if is_downloaded else 0
        })
    
    return jsonify(laws_list)

@app.route("/api/laws/scrape/<law_id>")
@login_required
def scrape_law(law_id):
    """سحب نظام معين وحفظه في القاعدة"""
    from laws_scraper import LawsScraper
    
    def generate():
        try:
            yield json.dumps({"status": "جاري السحب...", "progress": 10}) + "\n"
            scraper = LawsScraper()
            law_data = scraper.get_law_details(law_id)
            scraper.close()
            
            if law_data:
                yield json.dumps({"status": "جاري الحفظ...", "progress": 80}) + "\n"
                laws_db_upsert(law_id, law_data)
                
                yield json.dumps({
                    "done": True, "progress": 100,
                    "lawName": law_data['lawName'],
                    "articlesCount": law_data['articlesCount']
                }) + "\n"
            else:
                yield json.dumps({"error": "فشل سحب النظام"}) + "\n"
        except Exception as e:
            yield json.dumps({"error": str(e)}) + "\n"
    
    return Response(generate(), mimetype='application/json')

batch_scrape_state = {"running": False, "total": 0, "done": 0, "current_law": "", "results": [], "errors": []}
batch_scrape_lock = threading.Lock()

def run_batch_scrape(law_ids, username):
    """ثريد سحب مجموعة أنظمة"""
    from laws_scraper import LawsScraper
    
    global batch_scrape_state
    with batch_scrape_lock:
        batch_scrape_state.update({"running": True, "total": len(law_ids), "done": 0, "current_law": "", "results": [], "errors": []})
    
    scraper = LawsScraper()
    
    for i, law_id in enumerate(law_ids):
        if not batch_scrape_state["running"]:
            break
        
        law_name = AVAILABLE_LAWS.get(law_id, law_id[:8])
        batch_scrape_state["current_law"] = law_name
        
        try:
            law_data = scraper.get_law_details(law_id)
            if law_data:
                laws_db_upsert(law_id, law_data)
                batch_scrape_state["results"].append({
                    "lawId": law_id, "lawName": law_data['lawName'],
                    "articlesCount": law_data['articlesCount'], "status": "success"
                })
            else:
                batch_scrape_state["errors"].append({"lawId": law_id, "lawName": law_name, "error": "فشل السحب"})
        except Exception as e:
            batch_scrape_state["errors"].append({"lawId": law_id, "lawName": law_name, "error": str(e)})
        
        batch_scrape_state["done"] = i + 1
        time.sleep(2)
    
    scraper.close()
    batch_scrape_state["running"] = False
    batch_scrape_state["current_law"] = ""

@app.route("/api/laws/scrape-batch", methods=["POST"])
@login_required
def scrape_batch():
    if batch_scrape_state["running"]:
        return jsonify({"error": "عملية سحب جارية بالفعل"}), 400
    
    data = request.get_json()
    law_ids = data.get("law_ids", [])
    
    if not law_ids:
        downloaded = laws_db_list_ids()
        law_ids = [lid for lid in AVAILABLE_LAWS.keys() if lid not in downloaded]
    
    if not law_ids:
        return jsonify({"error": "جميع الأنظمة محملة بالفعل"}), 400
    
    t = threading.Thread(target=run_batch_scrape, args=(law_ids, session.get('user')), daemon=True)
    t.start()
    return jsonify({"status": "started", "total": len(law_ids)})

@app.route("/api/laws/scrape-batch/status")
@login_required
def scrape_batch_status():
    return jsonify({k: batch_scrape_state[k] for k in ["running", "total", "done", "current_law", "results", "errors"]})

@app.route("/api/laws/scrape-batch/stop", methods=["POST"])
@login_required
def scrape_batch_stop():
    batch_scrape_state["running"] = False
    return jsonify({"status": "stopped"})

@app.route("/api/laws/details/<law_id>")
@login_required
def get_law_details(law_id):
    law = laws_db_get(law_id)
    if not law:
        return jsonify({"error": "النظام غير محمّل"}), 404
    return jsonify(law)

@app.route("/api/laws/<law_id>")
@login_required
def get_law_by_id(law_id):
    law = laws_db_get(law_id)
    if not law:
        return jsonify({"success": False, "error": "النظام غير مستورد"}), 404
    return jsonify({"success": True, "law": law})

@app.route("/api/laws/download/<law_id>")
@login_required
def download_law(law_id):
    from urllib.parse import quote
    law = laws_db_get(law_id)
    if not law:
        return jsonify({"error": "النظام غير محمّل"}), 404
    
    law_name = law.get('lawName', AVAILABLE_LAWS.get(law_id, "نظام"))
    content = json.dumps(law, ensure_ascii=False, indent=2)
    arabic_filename = quote(f"{law_name}.json")
    
    return Response(
        content,
        mimetype='application/json; charset=utf-8',
        headers={'Content-Disposition': f"attachment; filename=\"{law_id}.json\"; filename*=UTF-8''{arabic_filename}"}
    )

@app.route("/api/laws/export/<law_id>")
@login_required
def export_law_json(law_id):
    from datetime import datetime
    from urllib.parse import quote
    
    data = laws_db_get(law_id)
    if not data:
        return jsonify({"error": "النظام غير محمّل"}), 404
    
    try:
        filter_type = request.args.get('filter', 'all')
        articles = data.get('articles', [])
        
        if filter_type == 'modified':
            articles = [a for a in articles if a.get('status') in ['معدلة', 'ملغية', 'محذوفة', 'مضافة'] or a.get('isModified')]
        
        export_data = {
            'lawId': data.get('lawId', law_id),
            'lawName': data.get('lawName', ''),
            'info': data.get('info', {}),
            'brief': data.get('brief', ''),
            'articlesCount': data.get('articlesCount', len(data.get('articles', []))),
            'exportInfo': {
                'exportDate': datetime.now().strftime('%Y-%m-%d'),
                'exportTime': datetime.now().strftime('%H:%M:%S'),
                'filterType': filter_type,
                'exportedArticlesCount': len(articles)
            },
            'articles': articles
        }
        
        law_name = data.get('lawName', 'law')
        filter_suffix = '_modified' if filter_type == 'modified' else ''
        simple_filename = f"law_export{filter_suffix}.json"
        arabic_filename = quote(f"{law_name}{('_معدلة' if filter_type == 'modified' else '')}.json")
        
        return Response(
            json.dumps(export_data, ensure_ascii=False, indent=2),
            mimetype='application/json; charset=utf-8',
            headers={'Content-Disposition': f"attachment; filename=\"{simple_filename}\"; filename*=UTF-8''{arabic_filename}"}
        )
    except Exception as e:
        return jsonify({"error": f"خطأ في التصدير: {str(e)}"}), 500

@app.route("/api/laws/search/<law_id>")
@login_required
def search_in_law(law_id):
    query = request.args.get('q', '').strip()
    if not query:
        return jsonify({"error": "أدخل نص البحث"}), 400
    
    data = laws_db_get(law_id)
    if not data:
        return jsonify({"error": "النظام غير محمّل"}), 404
    
    results = []
    for i, article in enumerate(data.get('articles', [])):
        if query in article.get('articleText', '') or query in article.get('articleTitle', ''):
            results.append({
                'index': i,
                'articleTitle': article.get('articleTitle'),
                'articleNumber': article.get('articleNumber'),
                'chapter': article.get('chapter'),
                'status': article.get('status'),
                'preview': article.get('articleText', '')[:200] + '...'
            })
    
    return jsonify({'query': query, 'resultsCount': len(results), 'results': results})

@app.route("/api/laws/search-all")
@login_required
def search_all_laws():
    """بحث شامل في كل الأنظمة"""
    query = request.args.get('q', '').strip()
    if not query or len(query) < 2:
        return jsonify({"error": "أدخل كلمة بحث (حرفين على الأقل)"}), 400
    
    db = load_laws_db()
    results = []
    laws_matched = set()
    
    for law_id, law_data in db.get("laws", {}).items():
        law_name = law_data.get("lawName", "")
        for i, article in enumerate(law_data.get("articles", [])):
            text = article.get("articleText", "")
            title = article.get("articleTitle", "")
            if query in text or query in title:
                match_pos = text.find(query)
                if match_pos == -1:
                    match_pos = 0
                start = max(0, match_pos - 60)
                end = min(len(text), match_pos + len(query) + 120)
                snippet = ("..." if start > 0 else "") + text[start:end] + ("..." if end < len(text) else "")
                
                results.append({
                    "lawId": law_id,
                    "lawName": law_name,
                    "articleIndex": i,
                    "articleTitle": title,
                    "articleNumber": article.get("articleNumber", ""),
                    "chapter": article.get("chapter", ""),
                    "status": article.get("status", "سارية"),
                    "snippet": snippet
                })
                laws_matched.add(law_id)
    
    return jsonify({
        "query": query,
        "totalResults": len(results),
        "lawsMatched": len(laws_matched),
        "results": results[:200]
    })


if __name__ == "__main__":
    print("\n" + "=" * 50)
    print("  🌐 سيرفر سحب الأحكام القضائية")
    print("  http://localhost:5000")
    print(f"  ⚡ {MAX_WORKERS} ثريدات متوازية")
    print(f"  👥 حتى {MAX_CONCURRENT_SESSIONS} مستخدمين")
    print("=" * 50 + "\n")
    app.run(debug=False, port=5000, threaded=True)
