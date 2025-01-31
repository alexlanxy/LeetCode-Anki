import json
import os.path
import pickle
import re
import time
import random
from sys import exit

import undetected_chromedriver as uc
import cloudscraper
from requests.cookies import RequestsCookieJar
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC

from database import Problem, ProblemTag, Tag, Submission, create_tables, Solution
from utils import destructure, random_wait, do, get

COOKIE_PATH = "./cookies.dat"


class LeetCodeCrawler:
    def __init__(self):
        # Use cloudscraper to bypass Cloudflare
        self.session = cloudscraper.create_scraper()

        # Set up undetected Selenium Chrome browser
        chrome_options = uc.ChromeOptions()
        chrome_options.add_argument("--disable-blink-features=AutomationControlled")
        self.browser = uc.Chrome(options=chrome_options, headless=False)  # Change to True for headless mode

        print("✅ Using Chrome version:", self.browser.capabilities["browserVersion"])

        self.session.headers.update(
            {
                'Host': 'leetcode.com',
                'Cache-Control': 'max-age=0',
                'Upgrade-Insecure-Requests': '1',
                'Referer': 'https://leetcode.com/accounts/login/',
                'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/132.0.0.0 Safari/537.36',
                'Accept': '*/*',
                'Accept-Encoding': 'gzip, deflate, br, zstd',
                'Accept-Language': 'en,zh-CN;q=0.9,zh;q=0.8,fr;q=0.7',
                'Connection': 'keep-alive',
                'Sec-Ch-Ua': '"Not A(Brand";v="8", "Chromium";v="132", "Google Chrome";v="132"',
                'Sec-Ch-Ua-Platform': '"macOS"',
                'Sec-Ch-Ua-Mobile': '?0',
                'Sec-Fetch-Dest': 'empty',
                'Sec-Fetch-Mode': 'cors',
                'Sec-Fetch-Site': 'same-origin',
            }
        )

    def login(self):
        """ Handles login and extracts cookies for authenticated API requests """
        browser_cookies = {}

        if os.path.isfile(COOKIE_PATH):
            with open(COOKIE_PATH, 'rb') as f:
                browser_cookies = pickle.load(f)
        else:
            print("😎 Starting browser login..., please fill the login form")
            try:
                login_url = "https://leetcode.com/accounts/login"
                self.browser.get(login_url)

                WebDriverWait(self.browser, 300).until(
                    lambda driver: "login" not in driver.current_url
                )
                time.sleep(10)
                browser_cookies = self.browser.get_cookies()

                # Save cookies for future use
                with open(COOKIE_PATH, 'wb') as f:
                    pickle.dump(browser_cookies, f)

                print("🎉 Login successful")


            except Exception as e:
                print(f"🤔 Login Failed: {e}, please try again")
                exit()

        # Transfer cookies to session
        cookies = RequestsCookieJar()
        for item in browser_cookies:
            cookies.set(item['name'], item['value'])

            # Extract Cloudflare clearance token
            if item['name'] == 'cf_clearance':
                self.session.cookies.set(item['name'], item['value'])

            # Extract CSRF token
            if item['name'] == 'csrftoken':
                self.session.headers.update({"x-csrftoken": item['value']})

        self.session.cookies.update(cookies)

    import json

    def fetch_accepted_problems(self):
        """ Fetches user's accepted problems from LeetCode with debugging """
        url = "https://leetcode.com/api/problems/all/"
        try:
            response = self.session.get(url)

            # Debugging: Print Status Code
            print(f"Status Code: {response.status_code}")

            # Check if request was successful
            if response.status_code != 200:
                print(f"⚠️ Request failed with status {response.status_code}")
                print(f"Response Text: {response.text}")
                return

            # Debugging: Print a snippet of the response
            print("✅ Request successful, parsing response...")

            all_problems = json.loads(response.content.decode('utf-8'))

            # Debugging: Print structure of received JSON
            print(json.dumps(all_problems, indent=2)[:1000])  # Print only first 1000 characters

            counter = 0
            for item in all_problems.get('stat_status_pairs', []):
                if item.get('status') == 'ac':
                    id, slug = destructure(item['stat'], "question_id", "question__title_slug")

                    # Debugging: Check if problem already exists
                    if Problem.get_or_none(Problem.id == id) is None:
                        counter += 1
                        print(f"🚀 Fetching problem: {slug} (ID: {id})")
                        do(self.fetch_problem, args=[slug, True])
                        do(self.fetch_solution, args=[slug])

                    do(self.fetch_submission, args=[slug])

            print(f"🤖 Updated {counter} problems")

        except json.JSONDecodeError as e:
            print(f"❌ JSON Decode Error: {e}")
            print(f"Response Content: {response.content[:500]}")
        except Exception as e:
            print(f"❌ Unexpected Error: {e}")

    def fetch_problem(self, slug, accepted=False):
        """ Fetches problem details """
        print(f"🤖 Fetching problem: {slug}...")
        self.random_delay()

        query_params = {
            'operationName': "getQuestionDetail",
            'variables': {'titleSlug': slug},
            'query': '''query getQuestionDetail($titleSlug: String!) {
                        question(titleSlug: $titleSlug) {
                            questionId
                            questionFrontendId
                            questionTitle
                            questionTitleSlug
                            content
                            difficulty
                            stats
                            similarQuestions
                            categoryTitle
                            topicTags {
                            name
                            slug
                        }
                    }
                }'''
        }

        resp = self.session.post(
            "https://leetcode.com/graphql",
            json=query_params,
            headers={"content-type": "application/json"}
        )

        body = json.loads(resp.content)
        question = get(body, 'data.question')

        Problem.replace(
            id=question['questionId'], display_id=question['questionFrontendId'], title=question["questionTitle"],
            level=question["difficulty"], slug=slug, description=question['content'],
            accepted=accepted
        ).execute()

        for item in question['topicTags']:
            if Tag.get_or_none(Tag.slug == item['slug']) is None:
                Tag.replace(name=item['name'], slug=item['slug']).execute()

            ProblemTag.replace(problem=question['questionId'], tag=item['slug']).execute()

    def fetch_solution(self, slug):
        """ Fetches solution for a problem """
        print(f"🤖 Fetching solution for: {slug}")
        self.random_delay()

        query_params = {
            "operationName": "QuestionNote",
            "variables": {"titleSlug": slug},
            "query": '''
            query QuestionNote($titleSlug: String!) {
                question(titleSlug: $titleSlug) {
                    questionId
                    solution {
                      id
                      content
                      contentTypeId
                      canSeeDetail
                      paidOnly
                    }
                }
            }
            '''
        }

        resp = self.session.post(
            "https://leetcode.com/graphql",
            json=query_params,
            headers={"content-type": "application/json"}
        )

        body = json.loads(resp.content)
        solution = get(body, "data.question")

        if solution['solution'] and not solution['solution']['paidOnly']:
            Solution.replace(
                problem=solution['questionId'],
                url=f"https://leetcode.com/articles/{slug}/",
                content=solution['solution']['content']
            ).execute()

    def fetch_submission(self, slug):
        print(f"🤖 Fetching submission for problem: {slug}")
        query_params = {
            'operationName': "Submissions",
            'variables': {"offset": 0, "limit": 20, "lastKey": '', "questionSlug": slug},
            'query': '''query Submissions($offset: Int!, $limit: Int!, $lastKey: String, $questionSlug: String!) {
                                        submissionList(offset: $offset, limit: $limit, lastKey: $lastKey, questionSlug: $questionSlug) {
                                        lastKey
                                        hasNext
                                        submissions {
                                            id
                                            statusDisplay
                                            lang
                                            runtime
                                            timestamp
                                            url
                                            isPending
                                            __typename
                                        }
                                        __typename
                                    }
                                }'''
        }
        resp = self.session.post("https://leetcode.com/graphql",
                                 data=json.dumps(query_params).encode('utf8'),
                                 headers={
                                     "content-type": "application/json",
                                 })
        body = json.loads(resp.content)

        # parse data
        submissions = get(body, "data.submissionList.submissions")
        if len(submissions) > 0:
            for sub in submissions:
                if Submission.get_or_none(Submission.id == sub['id']) is not None:
                    continue

                if sub['statusDisplay'] == 'Accepted':
                    url = sub['url']
                    self.browser.get(f'https://leetcode.com{url}')
                    element = WebDriverWait(self.browser, 10).until(
                        EC.presence_of_element_located((By.ID, "result_date"))  # 用实际等待元素的ID替换"someId"
                    )
                    html = self.browser.page_source
                    pattern = re.compile(
                        r'submissionCode: \'(?P<code>.*)\',\n  editCodeUrl', re.S
                    )
                    matched = pattern.search(html)
                    code = matched.groupdict().get('code') if matched else None
                    if code:
                        Submission.insert(
                            id=sub['id'],
                            slug=slug,
                            language=sub['lang'],
                            created=sub['timestamp'],
                            source=code.encode('utf-8')
                        ).execute()
                    else:
                        raise Exception(f"Cannot get submission code for problem: {slug}")

    def random_delay(self):
        """ Adds a delay to mimic human behavior """
        wait_time = random.uniform(5, 15)
        print(f"⏳ Waiting {wait_time:.2f} seconds...")
        time.sleep(wait_time)


if __name__ == '__main__':
    create_tables()
    crawler = LeetCodeCrawler()
    crawler.login()
    crawler.fetch_accepted_problems()
