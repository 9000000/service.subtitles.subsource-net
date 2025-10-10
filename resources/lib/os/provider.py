import io
import re
import zipfile
import xbmcaddon
from requests import Session, HTTPError, RequestException

from resources.lib.exceptions import ProviderError, ServiceUnavailable, TooManyRequests
from resources.lib.utilities import log

API_URL = "https://api.subsource.net/api/v1"
SEARCH_URL = f"{API_URL}/movies/search"
GET_SUB_URL = f"{API_URL}/subtitles"

def logging(msg):
    return log(__name__, msg)

class SubtitlesProvider:
    def __init__(self):
        self.addon = xbmcaddon.Addon()
        self.api_key = self.addon.getSetting('subsource.apikey')
        self.session = Session()
        self.session.headers.update({'X-API-Key': self.api_key})

    def _request(self, method, url, params=None, stream=False):
        if not self.api_key:
            raise ProviderError("Subsource API Key is missing.")
        try:
            r = self.session.request(method, url, params=params, timeout=10, stream=stream)
            r.raise_for_status()
            if stream:
                return r.content
            return r.json()
        except HTTPError as e:
            status_code = e.response.status_code
            if status_code == 429:
                raise TooManyRequests("Too many requests to Subsource API.")
            elif status_code in [502, 503, 504]:
                raise ServiceUnavailable(f"Subsource API is unavailable (status code: {status_code}).")
            else:
                logging(f"Subsource API request failed: {e.response.text}")
                raise ProviderError(f"Subsource API error: {status_code}")
        except RequestException as e:
            logging(f"Subsource API request failed: {e}")
            raise ServiceUnavailable(f"Could not connect to Subsource API: {e}")

    def parse_filename(self, filename):
        filename = re.sub(r'\[.*?\]', '', filename).strip()
        clean_name = re.sub(r'\.\d+p.*|\.(mkv|avi|mp4)$', '', filename)
        clean_name = re.sub(r'\(.*?\)', '', clean_name).strip()
        clean_name = re.sub(r'\.(?=[A-Z])', ' ', clean_name)
        clean_name = re.sub(r'\.', ' ', clean_name)
        clean_name = re.sub(r'\s+', ' ', clean_name)
        year_match = re.search(r'\b(19[0-9]{2}|20[0-9]{2})\b', clean_name)
        year = year_match.group(0) if year_match else None
        series_match = re.search(r'S(\d+)E(\d+)', filename, re.IGNORECASE)
        if series_match:
            type_content = 'episode'
            season = int(series_match.group(1))
            episode = int(series_match.group(2))
            title = re.sub(r'\s*S\d+E\d+.*', '', clean_name[:year_match.start() if year_match else None]).strip()
            title = title.rstrip('.').rstrip()
        else:
            type_content = 'movie'
            season = None
            episode = None
            title = clean_name[:year_match.start()].strip().rstrip('.') if year_match else clean_name.rstrip('.')

        return {
            "title": title,
            "year": year,
            "type": type_content,
            "season": season,
            "episode": episode
        }

    def search_subtitles(self, media_data: dict, languages: str):
        if 'query' in media_data and ('title' not in media_data or not media_data['title']):
            parsed_data = self.parse_filename(media_data['query'])
            parsed_data['query'] = media_data['query']
            media_data = parsed_data

        is_tvshow = media_data.get('type') in ['episode', 'tvshow']
        query_title = media_data.get('tvshowtitle') or media_data.get('title', '')
        year = media_data.get('year', '')

        search_params = {
            "searchType": "text",
            "q": query_title,
            "year": year,
            "type": "tvseries" if is_tvshow else "movie"
        }

        logging(f"Searching Subsource with params: {search_params}")
        search_results = self._request("GET", SEARCH_URL, params=search_params)

        if "error" in search_results:
            logging(f"Subsource API error: {search_results.get('message')}")
            return []

        found_movies = search_results.get("data", [])
        if not found_movies:
            logging("No movies found on Subsource for the query.")
            return []

        movie_id = None
        first_season_id = None
        for res in found_movies:
            if is_tvshow:
                if res.get("season") == 1:
                    first_season_id = res.get("movieId")
                if str(res.get("season")) == str(media_data.get("season")):
                    movie_id = res.get("movieId")
                    break
            else:
                movie_id = res.get("movieId")
                break

        target_movie_id = movie_id or first_season_id
        if not target_movie_id:
            logging("Could not determine a movie ID from Subsource search results.")
            return []

        logging(f"Found movie ID: {target_movie_id}")
        sub_params = {"movieId": target_movie_id, "language": languages.lower()}
        subs_data = self._request("GET", GET_SUB_URL, params=sub_params)

        if not subs_data.get("success"):
            logging("Failed to get subtitles from Subsource.")
            return []

        all_subtitles = []
        for result in subs_data.get("data", []):
            release_name = result.get("releaseInfo", [""])[0]
            if is_tvshow:
                season_ep = f"S{int(media_data.get('season', '0')):02d}E{int(media_data.get('episode', '0')):02d}"
                if season_ep not in release_name:
                    continue

            sync = "true" if media_data.get('query') and media_data.get('query') in release_name else "false"
            sub = {
                'lang': result.get("language", "").capitalize(),
                'releaseName': release_name,
                'subId': result.get("subtitleId"),
                'sync': sync,
                'hearingImpaired': "true" if result.get("hearingImpaired") else "false",
            }
            all_subtitles.append(sub)

        logging(f"Found {len(all_subtitles)} subtitles from Subsource.")
        return all_subtitles

    def download_subtitle(self, query: dict):
        subtitle_id = query["id"]
        download_url = f"{GET_SUB_URL}/{subtitle_id}/download"
        logging(f"Downloading subtitle from: {download_url}")

        try:
            zipped_content = self._request("GET", download_url, stream=True)
            with zipfile.ZipFile(io.BytesIO(zipped_content)) as z:
                subtitle_filename = z.namelist()[0]
                file_content = z.read(subtitle_filename)
            return file_content
        except zipfile.BadZipFile as e:
            logging(f"Failed to unzip downloaded file: {e}")
            raise ProviderError("Downloaded file is not a valid zip file.")
        except IndexError:
            logging("Downloaded zip file is empty.")
            raise ProviderError("Downloaded zip file is empty.")
        except Exception as e:
            logging(f"An unexpected error occurred during download: {e}")
            raise ProviderError(f"An unexpected error occurred during download: {e}")