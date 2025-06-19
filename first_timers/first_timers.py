# -*- coding: utf-8 -*-
from __future__ import print_function
import re
from datetime import datetime
import requests
import tweepy
import logging
import time

# Set up logging
logging.basicConfig(filename='app.log', level=logging.INFO)

DAYS_OLD = 15
MAX_TWEETS_LEN = 280

ellipse = u'…'
api_base = 'https://api.github.com/search/issues'
FIRST_ISSUE_QUERY_URL = api_base + '?q=label:"{}"&per_page=30&page=1&state=open&sort=updated&order=desc'

# Logging helper functions
def log_info(message):
    logging.info(f"{datetime.now()}: {message}")

def log_warning(message):
    logging.warning(f"{datetime.now()}: {message}")

def log_error(message):
    logging.error(f"{datetime.now()}: {message}")


def humanize_url(api_url: str) -> str:
    """Make an API endpoint to a Human endpoint."""
    match = re.match(
        r'https://api\.github\.com/repos/([^/]+)/([^/]+)/issues/(\d+)', api_url)
    if not match:
        raise ValueError(f'Format of API URLs has changed: {api_url}')
    user, repo, issue_num = match.groups()
    return f'https://github.com/{user}/{repo}/issues/{issue_num}'


def get_first_timer_issues(issue_label: str, github_token: str = None) -> list:
    """Fetches the first page of issues with the label first-timers-label
    which are still open.
    """
    headers = {}
    if github_token:
        headers['Authorization'] = f'token {github_token}'
    
    try:
        res = requests.get(
            FIRST_ISSUE_QUERY_URL.format(issue_label), 
            headers=headers,
            timeout=30
        )
        res.raise_for_status()
        
        data = res.json()
        if 'items' not in data:
            log_warning(f"No 'items' found in response for label: {issue_label}")
            return []
            
        items = [item for item in data['items']
                if check_days_passed(item['created_at'], DAYS_OLD)]
        
        log_info(f"Found {len(items)} fresh issues for label: {issue_label}")
        return items
        
    except requests.exceptions.RequestException as e:
        log_error(f'Error fetching issues: {str(e)}')
        return []
    except ValueError as e:
        log_error(f'Error parsing JSON response: {str(e)}')
        return []


def check_days_passed(date_created: str, days: int) -> bool:
    """Check if the issue was created within the specified number of days."""
    try:
        created_at = datetime.strptime(date_created, "%Y-%m-%dT%H:%M:%SZ")
        return (datetime.now() - created_at).days < days
    except ValueError as e:
        log_error(f'Error parsing date {date_created}: {str(e)}')
        return False


def add_repo_languages(issues, github_token: str = None):
    """Adds the repo languages to the issues list."""
    headers = {}
    if github_token:
        headers['Authorization'] = f'token {github_token}'
    
    for issue in issues:
        try:
            query_languages = issue['repository_url'] + '/languages'
            res = requests.get(query_languages, headers=headers, timeout=30)
            
            if res.status_code == 403:
                log_warning('Rate limit reached getting languages')
                time.sleep(60)  # Wait before continuing
                return issues
            elif res.status_code == 404:
                log_warning(f'Repository not found: {query_languages}')
                issue['languages'] = {}
            elif res.ok:
                languages = res.json()
                # Get top 3 languages by bytes
                sorted_langs = sorted(languages.items(), key=lambda x: x[1], reverse=True)
                issue['languages'] = dict(sorted_langs[:3])
            else:
                log_warning(f'Could not get languages for {query_languages}: {res.status_code}')
                issue['languages'] = {}
                
        except requests.exceptions.RequestException as e:
            log_error(f'Network error getting languages: {str(e)}')
            issue['languages'] = {}
        except Exception as e:
            log_error(f'Unexpected error getting languages: {str(e)}')
            issue['languages'] = {}
            
    return issues


def get_fresh(old_issue_list, new_issue_list):
    """Returns which issues are not present in the old list of issues."""
    if not old_issue_list:
        return new_issue_list
    
    old_urls = {x['url'] for x in old_issue_list if 'url' in x}
    return [x for x in new_issue_list if x.get('url') not in old_urls]


def tweet_issues(issues, creds, debug=False):
    """Takes a list of issues and credentials and tweets through the account
    associated with the credentials.
    Also takes a parameter 'debug', which can prevent actual tweeting.
    Returns a list of tweets.
    """
    if not issues:
        log_info("No issues to tweet")
        return []

    # Validate credentials
    required_keys = ['Consumer Key', 'Consumer Secret', 'Access Token', 'Access Token Secret']
    for key in required_keys:
        if key not in creds:
            log_error(f'Missing credential: {key}')
            return []

    try:
        # Twitter API v2 client
        client = tweepy.Client(
            consumer_key=creds['Consumer Key'],
            consumer_secret=creds['Consumer Secret'],
            access_token=creds['Access Token'],
            access_token_secret=creds['Access Token Secret'],
            wait_on_rate_limit=True
        )
        
        # Test authentication
        me = client.get_me()
        log_info(f"Authenticated as: {me.data.username}")
        
    except Exception as e:
        log_error(f'Twitter authentication failed: {str(e)}')
        return []

    # Estimate URL length (Twitter shortens URLs)
    url_len = 23  # Twitter's t.co shortened URL length
    base_hashtags = "#github #opensource"

    tweets = []

    for issue in issues:
        try:
            title = issue.get('title', 'No title')
            
            # Build language hashtags
            language_hashtags = ''
            if 'languages' in issue and issue['languages']:
                # Clean language names for hashtags
                lang_tags = []
                for lang in list(issue['languages'].keys())[:2]:  # Max 2 languages
                    clean_lang = re.sub(r'[^a-zA-Z0-9]', '', lang)
                    if clean_lang:
                        lang_tags.append(f'#{clean_lang}')
                language_hashtags = ' ' + ' '.join(lang_tags) if lang_tags else ''

            all_hashtags = base_hashtags + language_hashtags
            
            # Calculate available space for title
            # Format: "title url hashtags"
            available_for_title = MAX_TWEETS_LEN - (url_len + 1) - (len(all_hashtags) + 1)
            
            # Truncate title if necessary
            if len(title) > available_for_title:
                title = title[:available_for_title - 1] + ellipse

            url = humanize_url(issue['url'])
            
            tweet_text = f'{title} {url} {all_hashtags}'
            
            # Double-check tweet length
            if len(tweet_text) > MAX_TWEETS_LEN:
                # Fallback: reduce hashtags
                tweet_text = f'{title} {url} {base_hashtags}'
                if len(tweet_text) > MAX_TWEETS_LEN:
                    # Last resort: truncate title more
                    max_title_len = MAX_TWEETS_LEN - (url_len + 1) - (len(base_hashtags) + 1)
                    title = title[:max_title_len - 1] + ellipse
                    tweet_text = f'{title} {url} {base_hashtags}'

            if debug:
                log_info(f'[DEBUG] Would tweet: {tweet_text}')
            else:
                response = client.create_tweet(text=tweet_text)
                log_info(f'Successfully tweeted issue: {issue["title"]} (ID: {response.data["id"]})')
                time.sleep(1)  # Rate limiting courtesy

            tweets.append({
                'error': None,
                'tweet': tweet_text,
                'issue_url': issue['url']
            })

        except Exception as e:
            error_msg = f'Error tweeting issue "{issue.get("title", "Unknown")}": {str(e)}'
            log_error(error_msg)
            
            tweets.append({
                'error': str(e),
                'tweet': tweet_text if 'tweet_text' in locals() else 'Failed to create tweet',
                'issue_url': issue.get('url', '')
            })

    return tweets


def limit_issues(issues, limit_len=100):
    """Limit the number of issues saved in our DB."""
    if not issues:
        return []
    
    try:
        sorted_issues = sorted(issues, key=lambda x: x.get('updated_at', ''), reverse=True)
        return sorted_issues[:limit_len]
    except Exception as e:
        log_error(f'Error limiting issues: {str(e)}')
        return issues[:limit_len]  # Fallback without sorting


# Example usage function
def main():
    """Example of how to use this module."""
    # Configuration
    issue_label = "good first issue"
    github_token = "your_github_token_here"  # Optional but recommended
    
    twitter_creds = {
        'Consumer Key': 'your_consumer_key',
        'Consumer Secret': 'your_consumer_secret',
        'Access Token': 'your_access_token',
        'Access Token Secret': 'your_access_token_secret'
    }
    
    try:
        # Get fresh issues
        log_info("Fetching issues...")
        issues = get_first_timer_issues(issue_label, github_token)
        
        if not issues:
            log_info("No fresh issues found")
            return
        
        # Add language information
        log_info("Adding language information...")
        issues = add_repo_languages(issues, github_token)
        
        # Tweet issues (debug mode)
        log_info("Tweeting issues...")
        tweets = tweet_issues(issues, twitter_creds, debug=True)
        
        log_info(f"Process completed. {len(tweets)} tweets processed.")
        
    except Exception as e:
        log_error(f"Main process error: {str(e)}")


if __name__ == "__main__":
    main()