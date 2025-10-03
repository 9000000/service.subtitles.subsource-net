
from typing import Union
import zipfile
import difflib
import io
import re
import json
from requests import Session, ConnectionError, HTTPError, ReadTimeout, Timeout, RequestException
from resources.lib.exceptions import AuthenticationError, ConfigurationError, DownloadLimitExceeded, ProviderError, \
    ServiceUnavailable, TooManyRequests, BadUsernameError
from resources.lib.cache import Cache
from resources.lib.utilities import log

API_URL = "https://api.subsource.net/api"

def logging(msg):
    return log(__name__, msg)

class SubtitlesProvider:
    def __init__(self):
        self.request_headers = {
            'accept': 'application/json, text/plain, */*',
            'accept-language': 'en-US,en;q=0.9,vi;q=0.8',
            'content-type': 'application/json',
            'origin': 'https://subsource.net',
            'priority': 'u=1, i',
            'referer': 'https://subsource.net/',
            'sec-ch-ua': '"Google Chrome";v="125", "Chromium";v="125", "Not.A/Brand";v="24"',
            'sec-ch-ua-mobile': '?0',
            'sec-ch-ua-platform': '"macOS"',
            'sec-fetch-dest': 'empty',
            'sec-fetch-mode': 'cors',
            'sec-fetch-site': 'same-site',
            'user-agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36'
            }
        self.session = Session()
        self.session.headers = self.request_headers
        # Use any other cache outside of module/Kodi
        self.cache = Cache(key_prefix="os_com")

    def handle_request(self, url, data):
        try:
            r = self.session.post(url, data=json.dumps(data))
            r.raise_for_status()
        except (ConnectionError, Timeout, ReadTimeout) as e:
            raise ServiceUnavailable(f"Unknown Error, empty response: {e.status_code}: {e!r}")
        except HTTPError as e:
            status_code = e.response.status_code
            if status_code == 429:
                raise TooManyRequests()
            elif status_code == 503:
                raise ProviderError(e)
            else:
                raise ProviderError(f"Bad status code: {status_code}")
        return r.json()

    def parse_filename(self, filename):
        filename = re.sub(r'\[.*?\]', '', filename).strip()
        clean_name = re.sub(r'\.\d+p.*|\.(mkv|avi|mp4)$', '', filename)
        clean_name = re.sub(r'\(.*?\)', '', clean_name).strip()
        clean_name = re.sub(r'\.(?=[A-Z])', ' ', clean_name)
        clean_name = re.sub(r'\.', ' ', clean_name)  # Replace remaining dots that might be separators
        clean_name = re.sub(r'\s+', ' ', clean_name)  
        year_match = re.search(r'\b(19[0-9]{2}|20[0-9]{2})\b', clean_name)
        year = year_match.group(0) if year_match else None
        series_match = re.search(r'S(\d+)E(\d+)', filename, re.IGNORECASE)
        if series_match:
            type_content = 'TVSeries'
            seasonIdx = int(series_match.group(1))
            episodeIdx = int(series_match.group(2))
            title = re.sub(r'\s*S\d+E\d+.*', '', clean_name[:year_match.start() if year_match else None]).strip()
            title = title.rstrip('.').rstrip()
        else:
            type_content = 'Movie'
            seasonIdx = None
            episodeIdx = None
            title = clean_name[:year_match.start()].strip().rstrip('.') if year_match else clean_name.rstrip('.')

        return {
            "title": title,
            "year": year,
            "type": type_content,
            "season_number": seasonIdx,
            "episode_number": episodeIdx
        }

    def search_subtitles(self, media_data: dict, languages: str):
        metadata = self.parse_filename(media_data['query'])
        logging(f"Parsed Metadata: {metadata}")

        data = {'query': metadata['title']}
        ep_index = None
        if metadata['type'] == 'TVSeries':
            ep_index = f"S{metadata['season_number']:02d}E{metadata['episode_number']:02d}"
            logging(f"Ep index: {ep_index}")

        response = self.handle_request(API_URL + "/searchMovie", data=data)
        logging(f"Search response: {response}")

        if not response.get('success'):
            logging("No successful response from searchMovie API.")
            return None

        all_subtitles = []
        for item in response.get('found', []):
            logging(f"Item: {item}")
            if self.is_match_item(item, metadata):
                logging(f"Matched Item: {item}")
                data = {'movieName': item['linkName']}
                if metadata['type'] == 'TVSeries':
                    data['season'] = f"season-{metadata['season_number']}"

                get_movie_response = self.handle_request(API_URL + "/getMovie", data=data)
                logging(f"Get movie response: {get_movie_response}")

                if get_movie_response.get('success') and 'subs' in get_movie_response:
                    subtitles = self.filter_subs_by_language_and_epindex(get_movie_response['subs'], languages, ep_index)
                    logging(f"Filtered Subtitles: {subtitles}")
                    all_subtitles.extend(subtitles)
                else:
                    logging(f"Failed to get movie details or no subs found for {item['linkName']}.")

        if not all_subtitles:
            logging("No matching subtitles found.")
            return None

        return all_subtitles

    def filter_subs_by_language_and_epindex(self, subs_list, target_languages, ep_index=None):

        target_languages_list = target_languages.split(',')

        if ep_index:
            return [sub for sub in subs_list if sub['lang'] in target_languages_list and ep_index in sub['releaseName']]
        else:
            return [sub for sub in subs_list if sub['lang'] in target_languages_list]

    def is_match_item(self, item, metadata):
        return self.is_match_year(item, metadata) and self.is_match_title(item['title'], metadata['title'])

    def is_match_year(self, item, metadata):
        return metadata['year'] is None or str(item.get('releaseYear')) == metadata['year']

    def is_match_title(self, query, title):
        return difflib.SequenceMatcher(None, query, title).ratio() > 0.7

    def download_subtitle(self, query: dict):
        data = {"id": query["file_id"]}
        download_link = API_URL + "/downloadSub"
        res = self.session.post(url=download_link, data=json.dumps(data))
        res.raise_for_status()

        try:
            response_data = res.json()
            if response_data.get("success") and response_data.get("link"):
                download_url = response_data["link"]
                subtitle_res = self.session.get(download_url)
                subtitle_res.raise_for_status()

                with zipfile.ZipFile(io.BytesIO(subtitle_res.content)) as z:
                    file_name = z.namelist()[0]
                    file_content = z.read(file_name)
                return file_content
            else:
                raise ProviderError("Failed to get download link from API.")
        except (zipfile.BadZipFile, KeyError, IndexError) as e:
            logging(f"Failed to handle subtitle download response: {e}")
            raise ProviderError(f"Failed to handle subtitle download: {e}")
    
