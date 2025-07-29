from flask import Flask, render_template, request, redirect, url_for, flash, session
import requests
import os
import toml
from jdatetime import datetime
import re
import time
import base64

app = Flask(__name__)
app.secret_key = os.urandom(24)

GITHUB_API_BASE = 'https://api.github.com'


class BlogManager:
    """GitHub Blog Manager for static site generation with Zola"""

    def __init__(self, token=None, repo_owner=None, repo_name=None):
        self.token = token
        self.repo_owner = repo_owner
        self.repo_name = repo_name
        self.headers = {'Authorization': f'token {token}'} if token else {}

    def get_user_repos(self):
        """Get list of all user repositories"""
        if not self.token:
            return []

        url = f'{GITHUB_API_BASE}/user/repos'
        response = requests.get(
            url, headers=self.headers, params={
                'per_page': 100})

        if response.status_code == 200:
            return response.json()
        return []

    def get_file_content(self, file_path):
        """Get file content from GitHub repository"""
        if not all([self.token, self.repo_owner, self.repo_name]):
            return None

        url = f'{GITHUB_API_BASE}/repos/{self.repo_owner}/{self.repo_name}/contents/{file_path}'
        response = requests.get(url, headers=self.headers)

        if response.status_code == 200:
            content = response.json()
            return base64.b64decode(content['content']).decode('utf-8')
        return None

    def update_file(self, file_path, content, commit_message):
        """Update or create file in GitHub repository"""
        if not all([self.token, self.repo_owner, self.repo_name]):
            return False

        url = f'{GITHUB_API_BASE}/repos/{self.repo_owner}/{self.repo_name}/contents/{file_path}'
        response = requests.get(url, headers=self.headers)

        encoded_content = base64.b64encode(
            content.encode('utf-8')).decode('utf-8')

        data = {
            'message': commit_message,
            'content': encoded_content
        }

        if response.status_code == 200:
            data['sha'] = response.json()['sha']

        response = requests.put(url, headers=self.headers, json=data)
        return response.status_code in [200, 201]

    def delete_file(self, file_path, commit_message):
        """Delete file from GitHub repository"""
        if not all([self.token, self.repo_owner, self.repo_name]):
            return False

        url = f'{GITHUB_API_BASE}/repos/{self.repo_owner}/{self.repo_name}/contents/{file_path}'
        response = requests.get(url, headers=self.headers)

        if response.status_code == 200:
            sha = response.json()['sha']
            data = {
                'message': commit_message,
                'sha': sha
            }
            response = requests.delete(url, headers=self.headers, json=data)
            return response.status_code == 200
        return False

    def list_content_files(self):
        """List markdown files in the content directory"""
        if not all([self.token, self.repo_owner, self.repo_name]):
            return []

        url = f'{GITHUB_API_BASE}/repos/{self.repo_owner}/{self.repo_name}/contents/content'
        response = requests.get(url, headers=self.headers)

        if response.status_code == 200:
            files = response.json()
            return [f for f in files if f['name'].endswith('.md')]
        return []

    def create_repo_from_template(
            self, new_repo_name, new_repo_description, template_owner, template_repo):
        """Create new repository from template repository"""
        if not all([self.token, self.repo_owner]):
            return False

        url = f"https://api.github.com/repos/{template_owner}/{template_repo}/generate"

        headers = {
            'Authorization': f'token {self.token}',
            'Accept': 'application/vnd.github.baptiste-preview+json'
        }

        data = {
            'owner': self.repo_owner,
            'name': new_repo_name,
            'description': new_repo_description,
            'private': False,
            'include_all_branches': True,
        }

        response = requests.post(url, headers=headers, json=data)
        return response.status_code == 201

    def add_token_to_action_secrets(self):
        """Add GitHub token to repository secrets for GitHub Actions"""
        if not all([self.token, self.repo_owner, self.repo_name]):
            return False

        url = f'https://api.github.com/repos/{self.repo_owner}/{self.repo_name}/actions/secrets/PERSONAL_TOKEN'

        key_url = f'https://api.github.com/repos/{self.repo_owner}/{self.repo_name}/actions/secrets/public-key'
        key_response = requests.get(key_url, headers=self.headers)

        if key_response.status_code != 200:
            return False

        key_data = key_response.json()
        public_key = key_data['key']
        key_id = key_data['key_id']

        try:

            from nacl import encoding, public
            public_key_obj = public.PublicKey(
                public_key.encode("utf-8"), encoding.Base64Encoder())
            sealed_box = public.SealedBox(public_key_obj)
            encrypted = sealed_box.encrypt(self.token.encode("utf-8"))
            encrypted_value = encoding.Base64Encoder().encode(encrypted).decode("utf-8")

            data = {
                "encrypted_value": encrypted_value,
                "key_id": key_id
            }

            response = requests.put(url, headers=self.headers, json=data)
            return response.status_code == 201
        except ImportError:

            print("Warning: PyNaCl not installed. Skipping GitHub Actions secret setup.")
            return False

    def delete_repo(self, repo_name):
        """Delete repository from GitHub"""
        if not all([self.token, self.repo_owner]):
            return False

        url = f'{GITHUB_API_BASE}/repos/{self.repo_owner}/{repo_name}'
        response = requests.delete(url, headers=self.headers)

        return response.status_code == 204


@app.route('/')
def index():
    """Home page with authentication form"""
    return render_template('index.html')


@app.route('/auth', methods=['POST'])
def authenticate():
    """Authenticate user with GitHub personal access token"""
    token = request.form.get('token')
    if not token:
        flash('Please enter your GitHub token')
        return redirect(url_for('index'))

    headers = {'Authorization': f'token {token}'}
    response = requests.get(f'{GITHUB_API_BASE}/user', headers=headers)

    if response.status_code == 200:
        session['token'] = token
        session['user'] = response.json()
        return redirect(url_for('dashboard'))
    else:
        flash('Invalid token')
        return redirect(url_for('index'))


@app.route('/dashboard')
def dashboard():
    """User dashboard showing available repositories"""
    if 'token' not in session:
        return redirect(url_for('index'))

    blog_manager = BlogManager(session['token'])
    repos = blog_manager.get_user_repos()

    return render_template('dashboard.html', repos=repos,
                           user=session.get('user'))


@app.route('/select_repo', methods=['POST'])
def select_repo():
    """Select repository to manage"""
    repo_full_name = request.form.get('repo')
    if not repo_full_name:
        flash('Please select a repository')
        return redirect(url_for('dashboard'))

    owner, name = repo_full_name.split('/')
    session['repo_owner'] = owner
    session['repo_name'] = name

    return redirect(url_for('manage_blog'))


@app.route('/manage')
def manage_blog():
    """Main blog management interface"""
    if not all(k in session for k in ['token', 'repo_owner', 'repo_name']):
        return redirect(url_for('index'))

    blog_manager = BlogManager(
        session['token'],
        session['repo_owner'],
        session['repo_name'])

    config_content = blog_manager.get_file_content('config.toml')
    config_data = {}
    if config_content:
        try:
            config_data = toml.loads(config_content)
        except Exception as e:
            flash(f'Error parsing config.toml: {str(e)}')

    posts = blog_manager.list_content_files()

    blog_url = f"https://{session['repo_owner']}.github.io/{session['repo_name']}"

    return render_template('manage.html',
                           config=config_data,
                           posts=posts,
                           repo_name=session['repo_name'],
                           blog_url=blog_url,
                           repo_owner=session['repo_owner'])


@app.route('/edit_config', methods=['GET', 'POST'])
def edit_config():
    """Edit blog configuration (config.toml)"""
    if not all(k in session for k in ['token', 'repo_owner', 'repo_name']):
        return redirect(url_for('index'))

    blog_manager = BlogManager(
        session['token'],
        session['repo_owner'],
        session['repo_name'])

    if request.method == 'POST':

        config_data = {
            'base_url': request.form.get('base_url', ''),
            'title': request.form.get('title', ''),
            'description': request.form.get('description', ''),
            'author': request.form.get('author', ''),
            'default_language': request.form.get('default_language', 'en'),
            'generate_feeds': request.form.get('generate_feeds') == 'on',
            'feed_filenames': ['atom.xml'],
            'taxonomies': [{'name': 'tags', 'rss': True, 'paginate_by': 5}],
            'markdown': {
                'highlight_code': True,
                'extra_syntaxes_and_themes': [],
                'highlight_theme': request.form.get('highlight_theme', 'base16-ocean-dark')
            },
            'extra': {
                'math': request.form.get('math') == 'on',
                'mermaid': request.form.get('mermaid') == 'on',
                'comment': request.form.get('comment') == 'on',
                'social_image': request.form.get('social_image', 'icons/github.svg'),
                'style': {},
                'profile': {
                    'name': request.form.get('profile_name', ''),
                    'bio': request.form.get('profile_bio', ''),
                    'avatar_url': request.form.get('avatar_url', 'icons/github.svg'),
                    'avatar_invert': request.form.get('avatar_invert') == 'on',
                    'social': [
                        {'name': 'github', 'url': request.form.get(
                            'github_url', '')},
                        {'name': 'www', 'url': request.form.get(
                            'website_url', '')},
                        {'name': 'rss', 'url': '$BASE_URL/atom.xml'}
                    ]
                },
                'menu': [
                    {'name': 'Projects', 'url': '$BASE_URL/projects'},
                    {'name': 'Archive', 'url': '$BASE_URL/archive'},
                    {'name': 'Tags', 'url': '$BASE_URL/tags'},
                    {'name': 'About', 'url': '$BASE_URL/about'}
                ],
                'footer': {
                    'since': int(request.form.get('footer_since', 2025)),
                    'license': request.form.get('footer_license', 'CC BY-SA 4.0'),
                    'license_url': request.form.get('footer_license_url', 'https://creativecommons.org/licenses/by-sa/4.0/deed')
                }
            }
        }

        if request.form.get('default_language') == 'fa':
            config_data['extra']['direction'] = {'direction': 'rtl'}

        config_content = toml.dumps(config_data)

        if blog_manager.update_file(
                'config.toml', config_content, 'Update blog configuration'):
            flash('Configuration saved successfully')
        else:
            flash('Error saving configuration')

        return redirect(url_for('manage_blog'))

    config_content = blog_manager.get_file_content('config.toml')
    config_data = {}
    if config_content:
        try:
            config_data = toml.loads(config_content)
        except Exception as e:
            flash(f'Error parsing config.toml: {str(e)}')

    return render_template('edit_config.html', config=config_data)


@app.route('/new_post', methods=['GET', 'POST'])
def new_post():
    """Create new blog post"""
    if not all(k in session for k in ['token', 'repo_owner', 'repo_name']):
        return redirect(url_for('index'))

    if request.method == 'POST':
        title = request.form.get('title', '')
        content = request.form.get('content', '')
        description = request.form.get('description', '')
        tags = request.form.get('tags', '')

        filename = re.sub(r'[^\w\s-]', '', title).strip()
        filename = re.sub(r'[-\s]+', '-', filename).lower()
        filename = f"{filename}.md"

        today = datetime.now()
        date_str = f"{today.year}-{today.month:02d}-{today.day:02d}"

        tags_list = [tag.strip() for tag in tags.split(',') if tag.strip()]

        front_matter = f"""+++
title = "{title}"
date = "{date_str}"
description = "{description}"
[taxonomies]
tags = {tags_list}
+++

{content}"""

        blog_manager = BlogManager(
            session['token'],
            session['repo_owner'],
            session['repo_name'])

        if blog_manager.update_file(
                f'content/{filename}', front_matter, f'Add new post: {title}'):
            flash('New post created successfully')
            return redirect(url_for('manage_blog'))
        else:
            flash('Error creating post')

    return render_template('new_post.html')


@app.route('/new_repo', methods=['GET', 'POST'])
def new_repo():
    """Create new blog repository from template"""
    if 'token' not in session:
        return redirect(url_for('index'))

    if request.method == 'POST':
        previous_repo = session.get('repo_name')
        repo_name = request.form.get('repo_name', '')
        repo_description = request.form.get('repo_description', '')

        if not repo_name:
            flash('Repository name is required')
            return render_template('create_repo.html')

        token = session.get('token')
        template_owner = 'daradege'
        template_repo = 'kita-farsi'
        new_owner = session.get('user', {}).get('login')

        if not new_owner:
            flash('Unable to get user information')
            return redirect(url_for('dashboard'))

        website_url = f"https://{new_owner}.github.io/{repo_name}"

        blog_manager = BlogManager(token, new_owner, previous_repo)

        if blog_manager.create_repo_from_template(
                repo_name, repo_description, template_owner, template_repo):

            time.sleep(3)

            blog_manager = BlogManager(token, new_owner, repo_name)

            blog_manager.add_token_to_action_secrets()

            config_content = blog_manager.get_file_content('config.toml')
            if config_content:
                try:
                    config_data = toml.loads(config_content)
                    config_data['base_url'] = website_url
                    blog_manager.update_file(
                        'config.toml', toml.dumps(config_data), 'Update base URL')
                    flash('Repository created successfully')
                    session['repo_owner'] = new_owner
                    session['repo_name'] = repo_name
                    return redirect(url_for('manage_blog'))
                except Exception as e:
                    flash(
                        f'Repository created but error updating config: {str(e)}')
            else:
                flash('Repository created but config.toml not found')
        else:
            flash('Error creating repository')

        return redirect(url_for('dashboard'))

    return render_template('create_repo.html')


@app.route('/delete_repo', methods=['POST'])
def delete_repo():
    """Delete current repository"""
    if not all(k in session for k in ['token', 'repo_owner', 'repo_name']):
        return redirect(url_for('index'))

    repo_name = session.get('repo_name')
    if not repo_name:
        flash('No repository selected')
        return redirect(url_for('manage_blog'))

    blog_manager = BlogManager(
        session['token'],
        session['repo_owner'],
        session['repo_name'])

    if blog_manager.delete_repo(repo_name):
        flash('Repository deleted successfully')
        session.pop('repo_name', None)
        session.pop('repo_owner', None)
        return redirect(url_for('dashboard'))
    else:
        flash('Error deleting repository')
        return redirect(url_for('manage_blog'))


@app.route('/edit_post/<filename>')
def edit_post(filename):
    """Edit existing blog post"""
    if not all(k in session for k in ['token', 'repo_owner', 'repo_name']):
        return redirect(url_for('index'))

    blog_manager = BlogManager(
        session['token'],
        session['repo_owner'],
        session['repo_name'])
    content = blog_manager.get_file_content(f'content/{filename}')

    if not content:
        flash('File not found')
        return redirect(url_for('manage_blog'))

    parts = content.split('+++')
    if len(parts) >= 3:
        front_matter = parts[1].strip()
        post_content = '+++'.join(parts[2:]).strip()

        title_match = re.search(r'title\s*=\s*"([^"]*)"', front_matter)
        desc_match = re.search(r'description\s*=\s*"([^"]*)"', front_matter)
        tags_match = re.search(
            r'tags\s*=\s*\[(.*?)\]',
            front_matter,
            re.DOTALL)

        post_data = {
            'filename': filename,
            'title': title_match.group(1) if title_match else '',
            'description': desc_match.group(1) if desc_match else '',
            'content': post_content,
            'tags': tags_match.group(1).replace('"', '').replace("'", '') if tags_match else ''
        }

        return render_template('edit_post.html', post=post_data)

    flash('Error parsing file')
    return redirect(url_for('manage_blog'))


@app.route('/update_post/<filename>', methods=['POST'])
def update_post(filename):
    """Update existing blog post"""
    if not all(k in session for k in ['token', 'repo_owner', 'repo_name']):
        return redirect(url_for('index'))

    title = request.form.get('title', '')
    content = request.form.get('content', '')
    description = request.form.get('description', '')
    tags = request.form.get('tags', '')

    today = datetime.now()
    date_str = f"{today.year}-{today.month:02d}-{today.day:02d}"

    tags_list = [tag.strip() for tag in tags.split(',') if tag.strip()]

    front_matter = f"""+++
title = "{title}"
date = "{date_str}"
description = "{description}"
[taxonomies]
tags = {tags_list}
+++

{content}"""

    blog_manager = BlogManager(
        session['token'],
        session['repo_owner'],
        session['repo_name'])

    if blog_manager.update_file(
            f'content/{filename}', front_matter, f'Update post: {title}'):
        flash('Post updated successfully')
    else:
        flash('Error updating post')

    return redirect(url_for('manage_blog'))


@app.route('/delete_post/<filename>', methods=['POST'])
def delete_post(filename):
    """Delete blog post"""
    if not all(k in session for k in ['token', 'repo_owner', 'repo_name']):
        return redirect(url_for('index'))

    blog_manager = BlogManager(
        session['token'],
        session['repo_owner'],
        session['repo_name'])

    if blog_manager.delete_file(
            f'content/{filename}', f'Delete post: {filename}'):
        flash('Post deleted successfully')
    else:
        flash('Error deleting post')

    return redirect(url_for('manage_blog'))


@app.route('/logout')
def logout():
    """Logout and clear session"""
    session.clear()
    flash('Logged out successfully')
    return redirect(url_for('index'))
