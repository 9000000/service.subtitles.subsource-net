
import os
import shutil
import sys
import uuid

import xbmcaddon
import xbmcgui
import xbmcplugin
import xbmcvfs

from resources.lib.data_collector import get_language_data, get_media_data, get_file_path, convert_language, \
    clean_feature_release_name, get_flag
from resources.lib.exceptions import AuthenticationError, ConfigurationError, DownloadLimitExceeded, ProviderError, \
    ServiceUnavailable, TooManyRequests, BadUsernameError
from resources.lib.file_operations import get_file_data
from resources.lib.os.provider import SubtitlesProvider
from resources.lib.utilities import get_params, log, error



__addon__ = xbmcaddon.Addon()
__scriptid__ = __addon__.getAddonInfo("id")

__profile__ = xbmcvfs.translatePath(__addon__.getAddonInfo("profile"))
__temp__ = xbmcvfs.translatePath(os.path.join(__profile__, "temp", ""))

if xbmcvfs.exists(__temp__):
    shutil.rmtree(__temp__)
xbmcvfs.mkdirs(__temp__)


class SubtitleDownloader:

    def __init__(self):

        log(__name__, sys.argv)

        self.sub_format = "srt"
        self.handle = int(sys.argv[1])
        self.params = get_params()
        self.query = {}
        self.subtitles = {}
        self.file = {}

        try:
            self.open_subtitles = SubtitlesProvider()
        except ConfigurationError as e:
            error(__name__, 32002, e)

    def handle_action(self):
        log(__name__, "action '%s' called" % self.params["action"])
        if self.params["action"] == "manualsearch":
            self.search(self.params['searchstring'])
        elif self.params["action"] == "search":
            self.search()
        elif self.params["action"] == "download":
            self.download()

    def search(self, query=""):
        file_data = get_file_data(get_file_path())
        language_data = get_language_data(self.params)

        if query:
            media_data = {"query": query}
        else:
            media_data = get_media_data()
            if "basename" in file_data:
                media_data["query"] = file_data["basename"]
            log(__name__, f"media_data '{media_data}'")

        try:
            self.subtitles = self.open_subtitles.search_subtitles(media_data, language_data['languages'])
        except TooManyRequests as e:
            error(__name__, 32001, f"Too many requests: {e}")
        except ServiceUnavailable as e:
            error(__name__, 32001, f"Service unavailable: {e}")
        except ProviderError as e:
            error(__name__, 32001, f"Provider error: {e}")
        except Exception as e:
            error(__name__, 32001, f"An unexpected error occurred: {e}")

        if self.subtitles and len(self.subtitles):
            log(__name__, f"Found {len(self.subtitles)} subtitles")
            self.list_subtitles()
        else:
            log(__name__, "No subtitles found")
            xbmcgui.Dialog().notification(__addon__.getAddonInfo('name'), 'No subtitles found', xbmcgui.NOTIFICATION_INFO, 5000)


    def download(self):
        try:
            file_content = self.open_subtitles.download_subtitle(
                {"file_id": self.params["id"], "sub_format": self.sub_format})

            if not file_content:
                raise ProviderError("Downloaded file is empty.")

            subtitle_path = os.path.join(__temp__, f"{str(uuid.uuid4())}.{self.sub_format}")
            log(__name__, f"subtitle_path '{subtitle_path}'")

            with open(subtitle_path, "wb") as tmp_file:
                tmp_file.write(file_content)

            list_item = xbmcgui.ListItem(label=subtitle_path)
            xbmcplugin.addDirectoryItem(handle=self.handle, url=subtitle_path, listitem=list_item, isFolder=False)

        except (TooManyRequests, ServiceUnavailable, ProviderError) as e:
            error(__name__, 32001, str(e))
        except Exception as e:
            error(__name__, 32001, f"An unexpected error occurred during download: {e}")


    def list_subtitles(self):
        if self.subtitles:
            for subtitle in self.subtitles:
                language = convert_language(subtitle.get('lang', ''), True)
                file_name = subtitle.get('releaseName', 'Unknown')
                list_item = xbmcgui.ListItem(label=language, label2=file_name)

                url = f"plugin://{__scriptid__}/?action=download&id={subtitle.get('subId')}"

                list_item.setArt({'thumb': get_flag(subtitle.get('lang', ''))})
                list_item.setProperty("sync", "true" if subtitle.get('sync') else "false")
                list_item.setProperty("hearing_imp", "true" if subtitle.get('hearingImpaired') else "false")

                xbmcplugin.addDirectoryItem(handle=self.handle, url=url, listitem=list_item, isFolder=False)

        xbmcplugin.endOfDirectory(self.handle)
