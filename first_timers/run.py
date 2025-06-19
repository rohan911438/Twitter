# -*- coding: utf-8 -*-
from __future__ import print_function
import click
import os
import sys
import json
import warnings
import requests
import first_timers as FT
from datetime import datetime
import logging

# Set up logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('cli_bot.log'),
        logging.StreamHandler()
    ]
)

logger = logging.getLogger(__name__)


def ensure_directory_exists(file_path):
    """Ensure the directory for the file path exists."""
    directory = os.path.dirname(file_path)
    if directory and not os.path.exists(directory):
        os.makedirs(directory)
        logger.info(f"Created directory: {directory}")


def updateDB(all_issues, db_path):
    """Truncate and write the new list of issues in the DB."""
    try:
        ensure_directory_exists(db_path)
        
        # Create a backup of existing DB
        if os.path.exists(db_path):
            backup_path = db_path + '.backup'
            import shutil
            shutil.copy2(db_path, backup_path)
            logger.info(f"Created backup: {backup_path}")
        
        with open(db_path, 'w', encoding='utf-8') as dbFile:
            limited_issues = FT.limit_issues(all_issues)
            json.dump(limited_issues, dbFile, indent=2, ensure_ascii=False)
            
        logger.info(f"Database updated with {len(limited_issues)} issues")
        
    except Exception as e:
        logger.error(f"Error updating database: {str(e)}")
        raise


def load_credentials(creds_path):
    """Load and validate Twitter credentials."""
    if not os.path.exists(creds_path):
        raise FileNotFoundError(f'Credentials file does not exist: {creds_path}')
    
    try:
        with open(creds_path, 'r', encoding='utf-8') as credsFile:
            creds = json.load(credsFile)
        
        # Validate required credentials
        required_keys = ['Consumer Key', 'Consumer Secret', 'Access Token', 'Access Token Secret']
        missing_keys = [key for key in required_keys if key not in creds]
        
        if missing_keys:
            raise ValueError(f'Missing required credentials: {missing_keys}')
        
        return creds
        
    except json.JSONDecodeError as e:
        raise ValueError(f'Invalid JSON in credentials file: {str(e)}')


def load_database(db_path):
    """Load existing database or return empty list."""
    if not os.path.exists(db_path):
        return []
    
    try:
        with open(db_path, 'r', encoding='utf-8') as dbFile:
            data = json.load(dbFile)
            if not isinstance(data, list):
                logger.warning("Database file doesn't contain a list, treating as empty")
                return []
            return data
    except json.JSONDecodeError as e:
        logger.error(f"Error parsing database file: {str(e)}")
        return []
    except Exception as e:
        logger.error(f"Error reading database file: {str(e)}")
        return []


@click.command()
@click.option('--only-save',
              is_flag=True,
              help='Do not post any tweets, just populate the DB.')
@click.option('--db-path',
              default='data/db.json',
              help='Database file path for storing issues.')
@click.option('--create',
              is_flag=True,
              help='Create the DB file if it doesn\'t exist.')
@click.option('--creds-path',
              default='credentials.json',
              help='File containing Twitter API credentials.')
@click.option('--github-token',
              envvar='GITHUB_TOKEN',
              help='GitHub personal access token (optional, can be set via GITHUB_TOKEN env var).')
@click.option('--debug',
              is_flag=True,
              help='Run in debug mode (does not actually tweet).')
@click.option('--labels',
              default='good first issue,good-first-issue,beginner-friendly',
              help='Comma-separated list of issue labels to search for.')
@click.version_option(version='2.0.0')
def run(only_save, db_path, create, creds_path, github_token, debug, labels):
    """
    GitHub Issues Twitter Bot CLI
    
    This tool fetches GitHub issues with specified labels and tweets them.
    It maintains a database to avoid duplicate tweets.
    
    Examples:
        python cli.py --only-save --create
        python cli.py --debug
        python cli.py --labels "good first issue,help wanted"
    """
    
    click.secho(f"Starting GitHub Issues Twitter Bot v2.0.0", fg='cyan', bold=True)
    logger.info("Bot started")
    
    # Handle database file logic
    db_exists = os.path.exists(db_path)
    
    if not db_exists and not create:
        click.secho(
            f'Database file "{db_path}" does not exist. Use --create to create it.',
            err=True, fg='red'
        )
        sys.exit(1)
    
    if db_exists and create:
        click.secho(
            f'Database file "{db_path}" already exists but --create was passed.',
            err=True, fg='yellow'
        )
        if not click.confirm('Do you want to continue anyway?'):
            sys.exit(1)
    
    # Load existing issues
    try:
        old_issues = load_database(db_path) if db_exists else []
        click.secho(f'Loaded {len(old_issues)} existing issues from database', fg='blue')
    except Exception as e:
        click.secho(f'Error loading database: {str(e)}', err=True, fg='red')
        sys.exit(1)
    
    # Parse labels
    issue_labels = [label.strip() for label in labels.split(',') if label.strip()]
    click.secho(f'Searching for issues with labels: {issue_labels}', fg='blue')
    
    # Fetch new issues from GitHub
    all_new_issues = []
    successful_labels = []
    
    for label in issue_labels:
        try:
            click.echo(f'Fetching issues for label: "{label}"...', nl=False)
            issues = FT.get_first_timer_issues(label, github_token)
            all_new_issues.extend(issues)
            successful_labels.append(label)
            click.secho(f' Found {len(issues)} issues', fg='green')
            
        except requests.HTTPError as e:
            if e.response.status_code == 403:
                click.secho(f' Rate limit reached for label "{label}"', fg='yellow')
                logger.warning(f'Rate limit reached for label: {label}')
            else:
                click.secho(f' HTTP Error {e.response.status_code} for label "{label}"', fg='red')
                logger.error(f'HTTP Error for label {label}: {str(e)}')
        except Exception as e:
            click.secho(f' Error fetching issues for label "{label}": {str(e)}', fg='red')
            logger.error(f'Error fetching issues for label {label}: {str(e)}')
    
    if not successful_labels:
        click.secho('No issues could be fetched from any label. Exiting.', fg='red')
        sys.exit(1)
    
    # Remove duplicates based on URL
    seen_urls = set()
    unique_new_issues = []
    for issue in all_new_issues:
        if issue.get('url') not in seen_urls:
            unique_new_issues.append(issue)
            seen_urls.add(issue['url'])
    
    click.secho(f'Total unique new issues found: {len(unique_new_issues)}', fg='blue')
    
    # Get fresh issues (not in old database)
    fresh_issues = FT.get_fresh(old_issues, unique_new_issues)
    click.secho(f'Fresh issues (not in database): {len(fresh_issues)}', fg='green' if fresh_issues else 'yellow')
    
    # Combine all issues for database update
    all_issues = fresh_issues + old_issues
    
    # Process fresh issues for tweeting
    if fresh_issues and not only_save:
        try:
            # Add language information
            click.echo('Adding repository language information...')
            fresh_issues = FT.add_repo_languages(fresh_issues, github_token)
            
            # Load Twitter credentials
            try:
                creds = load_credentials(creds_path)
                click.secho('Twitter credentials loaded successfully', fg='green')
            except Exception as e:
                click.secho(f'Error loading credentials: {str(e)}', err=True, fg='red')
                sys.exit(1)
            
            # Show what will be tweeted
            click.secho(f'\nPreparing to tweet {len(fresh_issues)} issue(s):', fg='cyan', bold=True)
            for i, issue in enumerate(fresh_issues, 1):
                repo_info = issue.get('repository_url', '').replace('https://api.github.com/repos/', '')
                click.secho(f'  {i}. {issue.get("title", "No title")} ({repo_info})', fg='blue')
                click.secho(f'     URL: {FT.humanize_url(issue["url"])}', fg='blue', dim=True)
            
            if debug:
                click.secho('\n[DEBUG MODE] - No actual tweets will be sent', fg='yellow', bold=True)
            elif not click.confirm(f'\nProceed with tweeting {len(fresh_issues)} issues?'):
                click.secho('Tweeting cancelled by user', fg='yellow')
                only_save = True
            
            if not only_save:
                # Tweet the issues
                click.echo('\nTweeting issues...')
                tweets = FT.tweet_issues(fresh_issues, creds, debug)
                
                # Report results
                successful_tweets = [t for t in tweets if t['error'] is None]
                failed_tweets = [t for t in tweets if t['error'] is not None]
                
                click.secho(f'\nTweeting Results:', fg='cyan', bold=True)
                click.secho(f'  Successful: {len(successful_tweets)}', fg='green')
                click.secho(f'  Failed: {len(failed_tweets)}', fg='red' if failed_tweets else 'green')
                
                # Show tweet details
                for tweet in tweets:
                    if tweet['error'] is None:
                        click.secho(f'  ✓ {tweet["tweet"]}', fg='green')
                    else:
                        click.secho(f'  ✗ {tweet["tweet"]}', fg='red')
                        click.secho(f'    Error: {tweet["error"]}', fg='red', dim=True)
                
        except Exception as e:
            click.secho(f'Error during tweet processing: {str(e)}', err=True, fg='red')
            logger.error(f'Tweet processing error: {str(e)}')
            # Continue to update database even if tweeting fails
    
    elif fresh_issues and only_save:
        click.secho('Skipping tweets (--only-save mode)', fg='yellow')
    
    elif not fresh_issues:
        click.secho('No fresh issues to process', fg='yellow')
    
    # Update database
    try:
        updateDB(all_issues, db_path)
        click.secho(f'Database updated successfully at: {db_path}', fg='green')
    except Exception as e:
        click.secho(f'Error updating database: {str(e)}', err=True, fg='red')
        sys.exit(1)
    
    # Summary
    click.secho(f'\nSummary:', fg='cyan', bold=True)
    click.secho(f'  Total issues in database: {len(all_issues)}', fg='blue')
    click.secho(f'  Fresh issues found: {len(fresh_issues)}', fg='blue')
    click.secho(f'  Labels processed: {len(successful_labels)}/{len(issue_labels)}', fg='blue')
    
    logger.info(f"Bot completed successfully. Fresh issues: {len(fresh_issues)}")
    click.secho('Bot completed successfully! 🎉', fg='green', bold=True)


if __name__ == '__main__':
    try:
        run()
    except KeyboardInterrupt:
        click.secho('\nBot interrupted by user', fg='yellow')
        sys.exit(1)
    except Exception as e:
        click.secho(f'Unexpected error: {str(e)}', err=True, fg='red')
        logger.error(f'Unexpected error: {str(e)}')
        sys.exit(1)