"""Updates the projects section of a README with the user's GitHub repos.

Positional arguments (order is part of the action's contract, do not reorder):
    1  GitHub personal access token
    2  File name to update (default: README.md)
    3  Workspace path (used only to guess the repo name if GITHUB_REPOSITORY is unset)
    4  Max description length (default: 50, 0 hides descriptions)
    5  Show fork counts (default: true)
    6  Sort by: stars | forks | updated (default: stars)
    7  Topic that marks a repo as a project (default: project)
    8  Max number of projects to list (default: 0 = no limit)
"""
import os
import re
import sys
from datetime import datetime, timezone

from github import Github

try:  # PyGithub >= 1.59 / 2.x
    from github import Auth
except ImportError:  # older PyGithub
    Auth = None

START_MARKER = '<!-- PROJECTS START -->'
END_MARKER = '<!-- PROJECTS END -->'
SORT_OPTIONS = ('stars', 'forks', 'updated')
TRUE_VALUES = {'true', '1', 'yes', 'y', 'on'}
FALSE_VALUES = {'false', '0', 'no', 'n', 'off'}


class ScriptError(Exception):
    """Fatal, user-facing error."""


# --------------------------------------------------------------------------
# Argument helpers
# --------------------------------------------------------------------------
def get_arg(index, default=''):
    """Return argv[index] stripped, or default if missing/blank."""
    try:
        value = sys.argv[index].strip()
    except IndexError:
        return default
    return value or default


def parse_bool(value, default, name):
    if value == '' or value is None:
        return default
    lowered = str(value).strip().lower()
    if lowered in TRUE_VALUES:
        return True
    if lowered in FALSE_VALUES:
        return False
    raise ScriptError(f"Invalid value for {name}: {value!r} (use true or false)")


def parse_int(value, default, name):
    if value == '' or value is None:
        return default
    try:
        number = int(str(value).strip())
    except ValueError:
        raise ScriptError(f"Invalid value for {name}: {value!r} (expected an integer)")
    if number < 0:
        raise ScriptError(f"{name} must be 0 or greater, got {number}")
    return number


# --------------------------------------------------------------------------
# GitHub helpers
# --------------------------------------------------------------------------
def make_client(token):
    if Auth is not None:
        return Github(auth=Auth.Token(token), per_page=100)
    return Github(token, per_page=100)


def requests_left(client):
    try:
        return client.rate_limiting[0]
    except Exception:
        return None


def get_topics(repo):
    """Topics are normally part of the list payload; fall back to an extra call."""
    try:
        topics = repo.topics
        if topics is not None:
            return topics
    except AttributeError:
        pass
    return repo.get_topics()


def to_timestamp(value):
    if value is None:
        return 0.0
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.timestamp()


def clean_description(text, limit):
    """Single-line description truncated to `limit` characters ('' if none or limit is 0)."""
    if not text or limit == 0:
        return ''
    text = re.sub(r'\s+', ' ', text).strip()
    if len(text) > limit:
        text = text[:limit].rstrip() + '...'
    return text


def collect_projects(user, topic, max_description):
    """Return a list of project dicts for the user's own public repos tagged with `topic`."""
    projects = []
    # type='owner' skips org repos and repos where the user is only a collaborator
    for repo in user.get_repos(type='owner'):
        if repo.private:
            continue  # never publish private repos in a public README
        if topic not in {t.lower() for t in get_topics(repo)}:
            continue

        last_update = repo.pushed_at or repo.updated_at or repo.created_at
        projects.append({
            'name': repo.name,
            'url': repo.html_url,
            'description': clean_description(repo.description, max_description),
            'stars': int(repo.stargazers_count or 0),
            'forks': int(repo.forks_count or 0),
            'updated': to_timestamp(last_update),
            'updated_on': last_update.strftime('%Y-%m-%d') if last_update else 'unknown',
        })
    return projects


def sort_projects(projects, sort_by):
    """Highest first. Ties are broken by most recent update, then by name (A-Z)."""
    ordered = sorted(projects, key=lambda p: p['name'].lower())
    if sort_by != 'updated':
        ordered.sort(key=lambda p: p['updated'], reverse=True)
    # sorted(..., reverse=True) keeps the relative order of equal items
    ordered.sort(key=lambda p: p[sort_by], reverse=True)
    return ordered


# --------------------------------------------------------------------------
# README rendering
# --------------------------------------------------------------------------
def format_entry(project, show_forks):
    if show_forks:
        stats = f"**{project['stars']}⭐, {project['forks']}** forks"
    else:
        stats = f"**{project['stars']}⭐**"
    line = f"* [{project['name']}]({project['url']}) {stats}"
    if project['description']:
        line += f" ({project['description']})"
    return line


def build_section(projects, show_forks):
    lines = [format_entry(p, show_forks) for p in projects]
    return '\n' + '\n'.join(lines) + '\n'


def replace_section(old_content, new_section):
    pattern = re.compile(
        re.escape(START_MARKER) + r'.*?' + re.escape(END_MARKER), re.DOTALL)
    if not pattern.search(old_content):
        raise ScriptError(
            f"Could not find {START_MARKER} ... {END_MARKER} in the file. "
            "Add both markers where the projects list should go.")
    replacement = START_MARKER + new_section + END_MARKER
    # lambda: avoids backslash/group interpretation of the replacement text
    return pattern.sub(lambda _m: replacement, old_content, count=1)


# --------------------------------------------------------------------------
# Main
# --------------------------------------------------------------------------
def main():
    token = get_arg(1)
    file_name = get_arg(2, 'README.md')
    workspace = get_arg(3)
    max_description = parse_int(get_arg(4), 50, 'max_repo_description')
    show_forks = parse_bool(get_arg(5), True, 'allow_forks')
    sort_by = get_arg(6, 'stars').lower()
    topic = get_arg(7, 'project').lower()
    max_projects = parse_int(get_arg(8), 0, 'max_projects')

    if not token:
        raise ScriptError("A GitHub token is required (gh_token input).")
    if sort_by not in SORT_OPTIONS:
        raise ScriptError(
            f"Invalid project_sort_by {sort_by!r}. Choose one of: {', '.join(SORT_OPTIONS)}")

    client = make_client(token)
    start = requests_left(client)
    print(f'Requests left at start of the script: {start}')

    user = client.get_user()
    username = user.login

    print("=====================REPO CHECK BEGINS=============================")
    projects = collect_projects(user, topic, max_description)
    print(f"Found {len(projects)} public repo(s) tagged with the '{topic}' topic")
    projects = sort_projects(projects, sort_by)
    if max_projects:
        projects = projects[:max_projects]
    for p in projects:
        print(f"  - {p['name']} ({p['stars']} stars, {p['forks']} forks, updated {p['updated_on']})")

    end = requests_left(client)
    print(f'Requests left at end of the script: {end}')
    if start is not None and end is not None:
        print(f'Requests consumed in this process: {start - end}')
    print("=====================REPO CHECK ENDS=============================")

    if not projects:
        print(f"No projects found, leaving {file_name} untouched. "
              f"Add the '{topic}' topic to the repos you want listed.")
        return

    # Target repo: prefer the Actions-provided owner/name, else guess from the workspace path
    target = os.environ.get('GITHUB_REPOSITORY')
    if not target:
        if not workspace:
            raise ScriptError("Cannot determine the target repository.")
        target = f"{username}/{os.path.basename(os.path.normpath(workspace))}"
    readme_repo = client.get_repo(target)

    # Read content and sha from the same place (the API) so we never overwrite newer remote changes
    contents = readme_repo.get_contents(file_name)
    if isinstance(contents, list):
        raise ScriptError(f"{file_name} is a directory, expected a file.")
    readme = contents.decoded_content.decode('utf-8')

    section = build_section(projects, show_forks)
    rewritten = replace_section(readme, section)

    print("=====================RESULTS=============================")
    if rewritten != readme:
        readme_repo.update_file(
            contents.path, "Updating Projects Section", rewritten, contents.sha)
        print("Repo contents updated")
    else:
        print("No change detected in file contents")


if __name__ == '__main__':
    try:
        main()
    except ScriptError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)
