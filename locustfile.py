from locust import HttpUser, task, between
import random
import time
import re
import gevent
from gevent.lock import Semaphore

# ====== Testing Parameters ======
QUIZ_CMID = 3        # e.g. /mod/quiz/view.php?id=17
NUM_PAGES = 20         # how many pages your quiz has
QUIZ_START_DELAY = 5  # 5 minutes = 300 seconds
# ==========================

_quiz_start_time = None
_quiz_start_lock = Semaphore()


def find_attempt_id_in_html(html: str):
    """Try to find an existing attempt id in the view page HTML."""
    m = re.search(r"/mod/quiz/attempt\.php\?attempt=(\d+)", html)
    if m:
        return m.group(1)
    return None


def extract_logintoken(html: str):
    m = re.search(r'name="logintoken" value="([^"]+)"', html)
    if not m:
        raise Exception("Could not find logintoken on login page")
    return m.group(1)


def extract_sesskey(html, action_url):
    # 1) Find the form with that action
    form_pattern = rf'<form[^>]*action="{re.escape(action_url)}"[^>]*>(.*?)</form>'
    form_match = re.search(form_pattern, html, flags=re.DOTALL)

    if not form_match:
        return None  # no matching form found

    form_html = form_match.group(1)

    # 2) Inside that form, find the sesskey input
    sesskey_pattern = r'<input[^>]*name="sesskey"[^>]*value="([^"]+)"'
    sesskey_match = re.search(sesskey_pattern, form_html)

    if not sesskey_match:
        raise Exception("Could not find sesskey")

    return sesskey_match.group(1)


class MoodleStudent(HttpUser):
    wait_time = between(1, 3)

    def on_start(self):
        """Pick a student, log in, schedule global start time once."""
        uid = random.randint(1, 100)
        self.username = f"student{uid:03d}"
        self.password = "Pass123!"
        self.has_taken_quiz = False

        # 1) login page
        r = self.client.get("/login/index.php", name="/login_page")
        logintoken = extract_logintoken(r.text)

        # 2) login POST
        self.client.post(
            "/login/index.php",
            data={
                "username": self.username,
                "password": self.password,
                "logintoken": logintoken,
            },
            name="/login",
            allow_redirects=True,
        )

        # 3) schedule global quiz start once
        global _quiz_start_time
        with _quiz_start_lock:
            if _quiz_start_time is None:
                _quiz_start_time = time.time() + QUIZ_START_DELAY
                print(f"[GLOBAL] Quiz start at {time.ctime(_quiz_start_time)}")

    @task
    def take_quiz_paged(self):
        """Wait for global start, then start/continue attempt and visit all pages."""
        global _quiz_start_time

        if self.has_taken_quiz:
            gevent.sleep(10)
            return

        # wait until global start time
        if _quiz_start_time is None:
            gevent.sleep(1)
            return

        now = time.time()
        if now < _quiz_start_time:
            gevent.sleep(_quiz_start_time - now)

        # 1) open quiz view
        view = self.client.get(
            f"/mod/quiz/view.php?id={QUIZ_CMID}",
            name="/mod/quiz/view",
        )

        html = view.text
        sesskey = extract_sesskey(html, "http://localhost:8080/mod/quiz/startattempt.php")

        # 2) see if an attempt already exists
        attempt_id = find_attempt_id_in_html(html)

        if attempt_id:
            print(f"[{self.username}] Continuing attempt {attempt_id}")
        else:
            # 3) no attempt yet -> find and call startattempt link
            
            #start_link = find_startattempt_link(html)
            #if not start_link:
            #    print(f"[{self.username}] No attempt and no start link found, giving up.")
            #    self.has_taken_quiz = True
            #    return

            #print(f"[{self.username}] Starting new attempt via {start_link}")
            #start_resp = self.client.get(
            #    start_link,
            #    name="/mod/quiz/startattempt",
            #    allow_redirects=True,
            #)

            
            start_resp = self.client.post(
                "/mod/quiz/startattempt.php",
                data={
                    "cmid": QUIZ_CMID,
                    "sesskey": sesskey,
                },
                name="/quiz_start_attempt",
                allow_redirects=True
            )
            print("+++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++")
            req = start_resp.request
         #   print("Sesskey:", sesskey)
         #   print("REQUEST METHOD:", req.method)
            print("REQUEST URL   :", req.url)
         #   print("REQUEST BODY  :", req.body)
         #   print("STATUS CODE   :", start_resp.status_code)
            print(req)
         #   print("+++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++")

            # final URL should be attempt.php?attempt=...&page=0
            m = re.search(r"attempt=(\d+)", req.url)
            if not m:
                print(f"[{self.username}] Could not extract attempt id from URL {req.url}")
                self.has_taken_quiz = True
                return

            attempt_id = m.group(1)
            print(f"[{self.username}] New attempt id {attempt_id}")

        # 4) walk through all pages
        #for page in range(NUM_PAGES):
        #    if (NUM_PAGES == 1):
        #        url = f"/mod/quiz/attempt.php?attempt={attempt_id}"
        #        self.client.get(url, name="/mod/quiz/attempt_page")
        #        gevent.sleep(random.uniform(1, 3))
        #        pass
        #    else:
        #        url = f"/mod/quiz/attempt.php?attempt={attempt_id}&page={page-1}"
        #        self.client.get(url, name="/mod/quiz/attempt_page")
        #        print(f"[{self.username}] Is attempting page {page-1}")
        #        gevent.sleep(random.uniform(1, 3))

        # 5) open summary page (like clicking "Finish attempt")
        summary_url = f"/mod/quiz/summary.php?attempt={attempt_id}"
        self.client.get(summary_url, name="/mod/quiz/summary")

        print(f"[{self.username}] Finished walking pages for attempt {attempt_id}")
        
        
        self.client.post(
            "/mod/quiz/processattempt.php",
            data= {
                "attempt": attempt_id,
                "finishattempt": 1,
                "timeup": 0,
                "slots": "",
                "cmid": QUIZ_CMID,
                "sesskey": sesskey
            },  name="/quiz_processattempt",
                allow_redirects=True
        )

        self.has_taken_quiz = True
