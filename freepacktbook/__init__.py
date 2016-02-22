from os import environ, mkdir, path
import re

from bs4 import BeautifulSoup
import requests
from slugify import slugify
from tqdm import tqdm

from .slack import SlackNotification


class ImproperlyConfiguredError(Exception):
    pass


class InvalidCredentialsError(Exception):
    pass


class FreePacktBook(object):

    base_url = 'https://www.packtpub.com'
    code_files_url = base_url + '/code_download/%(id)s'
    download_url = base_url + '/ebook_download/%(book_id)s/%(format)s'
    my_books_url = base_url + '/account/my-ebooks'
    url = base_url + '/packt/offers/free-learning/'

    book_formats = ['epub', 'mobi', 'pdf']

    def __init__(self, email=None, password=None):
        self.session = requests.Session()
        self.email = email
        self.password = password

    def auth_required(func, *args, **kwargs):
        def decorated(self, *args, **kwargs):
            if 'SESS_live' not in self.session.cookies:
                response = self.session.post(self.url, {
                    'email': self.email,
                    'password': self.password,
                    'form_id': 'packt_user_login_form'})
                page = BeautifulSoup(response.text, 'html.parser')
                error = page.find('div', {'class': 'messages error'})
                if error:
                    raise InvalidCredentialsError(error.getText())
            return func(self, *args, **kwargs)
        return decorated

    def download_file(self, url, file_path, override=False):
        if not path.exists(path.dirname(file_path)):
            mkdir(path.dirname(file_path))
        if not path.exists(file_path) or override:
            response = self.session.get(url, stream=True)
            with open(file_path, 'wb') as f:
                for chunk in response.iter_content(chunk_size=1024):
                    if chunk:
                        f.write(chunk)

    @auth_required
    def claim_free_ebook(self):
        book = self.get_book_details()
        response = self.session.get(book['claim_url'])
        assert response.status_code == 200
        book.update({'url': self.url})
        return book

    def get_book_details(self, page=None):
        if page is None:
            response = self.session.get(self.url)
            page = BeautifulSoup(response.text, 'html.parser')
        summary = page.find('div', {'class': 'dotd-main-book-summary'})
        main_book_image = page.find('div', {'class': 'dotd-main-book-image'})
        claim_url = page.find('div', {'class': 'free-ebook'}).a['href']
        book_id = re.search(r'claim/(\d+)/', claim_url).groups()[0]
        return {
            'title': summary.find('div', {'class': 'dotd-title'}).getText().strip(),
            'description': summary.find('div', {'class': None}).getText().strip(),
            'book_url': self.base_url + main_book_image.a['href'],
            'image_url': 'https:%s' % main_book_image.img['src'],
            'claim_url': self.base_url + claim_url,
            'id': book_id}

    @auth_required
    def download_book(self, book, destination_dir='.', formats=None,
                      override=False):
        if formats is None:
            formats = self.book_formats
        pbar = tqdm(formats, leave=True, desc='Downloading %s' % book['title'])
        for book_format in pbar:
            url = self.download_url % {
                'book_id': book['id'], 'format': book_format}
            file_path = '%s/%s.%s' % (
                destination_dir, slugify(book['title']), book_format)
            self.download_file(url, file_path, override=override)

    @auth_required
    def download_code_files(self, book, destination_dir='.', override=False):
        url = self.code_files_url % {'id': int(book['id']) + 1}
        file_path = '%s/%s_code.zip' % (
            destination_dir, slugify(book['title']))
        self.download_file(url, file_path, override=override)

    @auth_required
    def my_books(self):
        books = []
        response = self.session.get(self.my_books_url)
        page = BeautifulSoup(response.text, 'html.parser')
        lines = page.find_all('div', {'class': 'product-line'})
        for line in lines:
            if not line.get('nid'):
                continue
            books.append({
                'title': line.find('div', {'class': 'title'}).getText().strip(),
                'book_url': self.base_url + line.find('div', {
                    'class': 'product-thumbnail'}).a['href'],
                'id': line['nid']})
        return books


def env_variables_required(variables):
    def decorated(func):
        def new_function():
            for variable in variables:
                if not variable in environ:
                    raise ImproperlyConfiguredError(
                        'Env variable %s is missing.' % variable)
            func()
        return new_function
    return decorated


@env_variables_required(['PACKTPUB_EMAIL', 'PACKTPUB_PASSWORD'])
def claim_free_ebook():
    client = FreePacktBook(
        environ.get('PACKTPUB_EMAIL'), environ.get('PACKTPUB_PASSWORD'))
    book = client.claim_free_ebook()

    if environ.get('PACKTPUB_BOOKS_DIR'):
        client.download_book(book, environ['PACKTPUB_BOOKS_DIR'])

    slack_notification = SlackNotification(
        environ.get('SLACK_URL'), environ.get('SLACK_CHANNEL'))
    slack_notification.notify(book)


@env_variables_required([
    'PACKTPUB_EMAIL', 'PACKTPUB_PASSWORD', 'PACKTPUB_BOOKS_DIR'])
def download_ebooks():
    client = FreePacktBook(
        environ.get('PACKTPUB_EMAIL'), environ.get('PACKTPUB_PASSWORD'))
    destination = environ.get('PACKTPUB_BOOKS_DIR')
    for book in client.my_books():
        client.download_book(book, destination_dir=destination)
