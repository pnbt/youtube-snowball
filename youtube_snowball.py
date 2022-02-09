# -*- coding: utf-8 -*-
# Author: Guillaume Chaslot

# Global Imports
import os
import json
import time
import re
import pickle
import argparse
import sys
import xlsxwriter
import collections
import datetime
import dateutil.relativedelta

from bs4 import BeautifulSoup
from urllib.request import urlopen

# Google Imports
import google.oauth2.credentials
import google_auth_oauthlib.flow

from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from google_auth_oauthlib.flow import InstalledAppFlow
from google.oauth2 import service_account

from urllib.parse import urlparse

# Number of videos that we need to scrap to understand a channel
REQUIRED_RECOS = 10
ESTIMATED_RECOS_PER_VIDEO = 15

# If True, reuse the latest channel stats that were obtained for that day.
REUSE_CHANNEL_STATS = True

# If True, only use API calls, but no scrapping
NO_SCRAPPING = False

# Max number of latest videos for each channel 
# (if number of recommendations needed is low, only a few of these latest videos will be considered)
LATEST_VIDEOS = 50

# The directory in which the data will be stored
DATA_DIRECTORY = 'channel-stats/'

API_SERVICE_NAME = 'youtube'
API_VERSION = 'v3'

SCOPES = [] # ["https://www.googleapis.com/auth/youtube.readonly"]

def readable_percent(number):
  return int(10000 * number)/100


class YouTubeApiClient():
  """ This class is a client that interfaces with the YouTube API."""

  def __init__(self):
    os.environ['OAUTHLIB_INSECURE_TRANSPORT'] = '1'
    self._client = self.get_authenticated_service()

  def get_authenticated_service(self):
    """ Create an authentificated client for YouTube API. """
    # We try to load from the client secret. There can be different client_secret_service on different platforms,
    # but they should have the same name.
    credentials = service_account.Credentials.from_service_account_file("client_secret_service.json")

    #flow = google_auth_oauthlib.flow.InstalledAppFlow.from_client_secrets_file(
    #    "client_secret_oauth.json", SCOPES)
    # credentials = flow.run_console()

    return build(API_SERVICE_NAME, API_VERSION, credentials=credentials)

  def remove_empty_kwargs(self, **kwargs):
    """ Removes keyword arguments that are not set. """
    good_kwargs = {}
    if kwargs is not None:
      for key, value in kwargs.items():
        if value:
          good_kwargs[key] = value
    return good_kwargs

  def try_to_do(self, the_function, **kwargs):
    """  Tries to perform a function, and in case of exception, sleep for 30 seconds.
 
        :param the_function: function we want to use
        :param kwargs: arguments that it will use
        :returns: the return value of the function
    """
    while True:
      print('Trying to run a call, we will try again if it fails')
      try:
        return the_function(**kwargs)
      except Exception as e:
        # In case of exception, we print it and sleep 30 seconds.
        print(e)
        # For an error with the playlist id, we just return none.
        if "The playlist identified with the request's <code>playlistId</code> parameter cannot be found." in repr(e):
          return None
        print('Sleeping 30 seconds')
        time.sleep(30)

  def channels_list_by_id(self, **kwargs):
    """ A wrapper for channels_list_by_id,
        that repeatedly tries to call this function. """
    return self.try_to_do(self.channels_list_by_id_try, **kwargs)

  def channels_list_by_id_try(self, **kwargs):
    """ Gets information about a channel from its id and parts required,
        that are passed in kwargs. """
    kwargs = self.remove_empty_kwargs(**kwargs)
    response = self._client.channels().list(
      **kwargs
    ).execute()

    if 'items' not in response:
        print('WARNING: no channel information for ' + repr(kwargs))
        return None
        raise Exception("No items in response")
    return response

  def playlists_list_by_id(self, **kwargs):
    """ A wrapper for playlists_list_by_id 
        that repeatedly tries to call this function. """
    return self.try_to_do(self.playlists_list_by_id_try, **kwargs)

  def playlists_list_by_id_try(self, **kwargs):
    """ Gets information about a playlist from its id and parts required,
        that are passed in kwargs. """
    kwargs = self.remove_empty_kwargs(**kwargs)

    response = self._client.playlistItems().list(
      **kwargs
    ).execute()

    return response

  def videos_list_multiple_ids(self, **kwargs):
    """ A wrapper for list_multiple_ids,
        that repeatedly tries to call this function. """
    return self.try_to_do(self.videos_list_multiple_ids_try, **kwargs)

  def videos_list_multiple_ids_try(self, **kwargs):
    """ Gets information about videos from its id and parts required,
        that are passed in kwargs. """
    kwargs = self.remove_empty_kwargs(**kwargs)

    response = self._client.videos().list(
      **kwargs
    ).execute()

    return response

  def search_list_related_videos(self, **kwargs):
    """ A wrapper for related videos,
        that repeatedly tries to call this function. """
    return self.try_to_do(self.search_list_related_videos_try, **kwargs)

  def search_list_related_videos_try(self, **kwargs):
    """ Gets information about related videos from its id and parts required,
        that are passed in kwargs. """
    kwargs = self.remove_empty_kwargs(**kwargs)

    response = self._client.search().list(
      **kwargs
    ).execute()

    return response

  def search_list_by_keyword(self, **kwargs):
    """ A wrapper for list_by_keyword,
        that repeatedly tries to call this function. """
    return self.try_to_do(self.search_list_by_keyword_try, **kwargs)

  def search_list_by_keyword_try(self, **kwargs):
    """ Gets information about videos from a keyword search,
        from its id and parts required, that are passed in kwargs. """
    kwargs = self.remove_empty_kwargs(**kwargs)

    response = self._client.search().list(
      **kwargs
    ).execute()

    return response


class Logger():
  def __init__(self):
      self._info = collections.defaultdict(int)
      self._warning = collections.defaultdict(int)

  def info(self, info):
    self._info[info] += 1

  def warning(self, warning):
    self._warning[warning] += 1

  def display(self):
    for i in self._info:
      print('INFO: ' + i + ' ' + repr(self._info[i]))

    for w in self._warning:
      print('WARNING: ' + w + ' ' + repr(self._warning[w]))


class YoutubeChannelScrapper():
  """ Class that scraps YouTube channels. """

  def __init__(self, youtube_client, folder, skip_older_videos=True):
    try:
      os.mkdir(DATA_DIRECTORY + folder)
    except:
      pass
    self._youtube_client = youtube_client
    self._folder = folder
    self._skip_older_videos = skip_older_videos

    # File names.
    self._channel_file = DATA_DIRECTORY + folder + '/all_channels'
    self._scrapped_videos_file = DATA_DIRECTORY + folder + '/scrapped_videos'
    self._api_video_file = DATA_DIRECTORY + folder + '/api_videos'
    self._video_to_chan_file = 'channel-stats/video_to_chan'

    # Data structures
    self._channel_stats = self.loadFromFile(self._channel_file)
    self._api_videos = self.loadFromFile(self._api_video_file)
    self._scrapped_videos = self.loadFromFile(self._scrapped_videos_file)
    self._video_to_chan_map = self.loadFromFile(self._video_to_chan_file)

    # Total number of recommendations for one channel
    self._total_channel_stats = collections.defaultdict(int)
    self._do_not_expand_channel_ids = set()

    # Channels to IDs mappings
    self._channel_name_to_id = {}
    self._channel_id_to_name = {}
    for video_id in self._api_videos:
      self._channel_name_to_id[self._api_videos[video_id]['snippet']['channelTitle']] = self._api_videos[video_id]['snippet']['channelId']
      self._channel_id_to_name[self._api_videos[video_id]['snippet']['channelId']] = self._api_videos[video_id]['snippet']['channelTitle']

    self._logger = Logger()

    self.make_video_to_chan_map()

  def make_video_to_chan_map(self):
    """ Creates the mapping between videos and their channels. """

    print('Making video to chan map, current has length '+ repr(len(self._video_to_chan_map)))

    video_to_get_by_api = ''
    video_to_get_by_api_nb = 0
    total_videos_got = 0

    # First looking at all scrapped videos, and calling YouTube API to get more info about them
    for video in self._scrapped_videos:
      # Looking for video if not in the api_videos
      if video not in self._api_videos and video not in self._video_to_chan_map:
        # The API calls allow to have information about 50 videos, so we call it when
        # we reach that number
        if video_to_get_by_api_nb == 50:
          self.getVideosFromYouTubeAPI(video_to_get_by_api)
          video_to_get_by_api = ''
          video_to_get_by_api_nb = 0

        if video_to_get_by_api != '':
          video_to_get_by_api += ','
        video_to_get_by_api += video
        video_to_get_by_api_nb += 1
        total_videos_got += 1

      # Getting api information about all recommendations
      for reco in self._scrapped_videos[video]['recommendations']:
        if total_videos_got % 1000 == 0 and total_videos_got > 0:
          self.saveToFile(self._video_to_chan_map, self._video_to_chan_file)
          print('Video to chan saved with length ' + repr(len(self._video_to_chan_map)))
          total_videos_got += 1

        if reco not in self._api_videos and reco not in self._video_to_chan_map:
          # The API calls allow to have information about 50 videos, so we call it when
          # we reach that number
          if video_to_get_by_api_nb == 50:
            self.getVideosFromYouTubeAPI(video_to_get_by_api)
            video_to_get_by_api = ''
            video_to_get_by_api_nb = 0

          if video_to_get_by_api != '':
            video_to_get_by_api += ','
          video_to_get_by_api += reco
          video_to_get_by_api_nb += 1
          total_videos_got += 1
  
    # Get the remaining videos if there are some.
    if video_to_get_by_api != '':
      self.getVideosFromYouTubeAPI(video_to_get_by_api)

    # Update the video to channel map.
    for video in self._api_videos:
      self._video_to_chan_map[video] = self._api_videos[video]['snippet']['channelId']
    self.saveToFile(self._video_to_chan_map, self._video_to_chan_file)
    print('Video to chan made with length ' + repr(len(self._video_to_chan_map)))

  def loadFromFile(self, filename):
    """ Loads a dictionary from a given json file. 
    
        :param filename: filename without the json extension
        :returns: extracted dictionary
    """

    print('Loading ' + filename + ' ...')
    try:
      with open(filename + '.json', "r") as json_file:
        my_dict = json.load(json_file)
    except:
      my_dict = {}
    print('Loaded ' + filename + ' with length: ' + repr(len(my_dict)))
    return my_dict

  def saveToFile(self, my_dict, filename):
    """ Saves an object to a given filename. 

        :param my_dict: dictionary to save
        :param filename: filename without json extension
        :returns: nothing
    """

    with open(filename + '.json', 'w') as fp:
      json.dump(my_dict, fp)

  def save_videos(self):
    """ Write files containing channel statistics, api data on videos,
        and data coming from scrapped videos. """

    # First we print the top views videos, for information.
    sorted_videos = sorted(self._api_videos, key=lambda k: int(self._api_videos[k].get('statistics', {}).get('viewCount', -1)), reverse=True)
    print('\n\n\n')
    print('Stats: ')
    # for video in sorted_videos[0:100]:
    #   try:
    #     print(repr(self._api_videos[video].get('statistics', {})['viewCount']) + ' - ' + self._api_videos[video]['snippet']['title'])
    #   except:
    #     print('WARNING, A VIDEO IN THE TOP 100 HAS NO VIEWCOUNT')
    self.printGeneralStats()

    # Now we save the videos
    print('saving...')
    self.saveToFile(self._channel_stats, self._channel_file)
    self.saveToFile(self._api_videos, self._api_video_file)
    self.saveToFile(self._scrapped_videos, self._scrapped_videos_file)
    self.saveToFile(self._video_to_chan_map, self._video_to_chan_file)
    print('Saved! ')
    print('')

  def clean_count(self, text_count):
      """ From a text that represent a count, extracts its integer value
      
          :param text_count: a string
          :returns: the count as an integer
      """

      # Ignore non ascii
      ascii_count = text_count.encode('ascii', 'ignore')
      # Ignore non numbers
      p = re.compile(r'[\d,]+')
      return int(p.findall(ascii_count.decode('utf-8'))[0].replace(',', ''))

  def getChannelForVideo(self, video):
    """ Returns the channel id for a given video. 
    
        :param: video id we want the channel of
        :returns: channel id for that given video
    """
    if video in self._api_videos:
      return self._api_videos[video]['snippet']['channelId']
    else:
      if video in self._video_to_chan_map:
        return self._video_to_chan_map[video]

    # If we don't have the video in the API, let's make a call to get it.
    self.getVideosFromYouTubeAPI(video)
    return self._api_videos[video]['snippet']['channelId']

  def parse_string(self, selector, pos=0):
      """ Extract one particular element from soup """
      return self.soup.select(selector)[pos].get_text().strip()

  def parse_int(self, selector, pos=0):
      """ Extract one integer element from soup """
      return int(re.sub("[^0-9]", "", self.parse_string(selector, pos)))

  def extract_number_or_default(self, text):
    pattern = re.compile(r'([0-9]*)')
    number_string = ''.join(pattern.findall(text))
    if len(number_string) == 0:
      return 0
    return int(number_string)

  def get_recommendations(self, video_id):
    """ Returns the recommendations for a given video. If it was not scrapped before,
        the video will be scrapped, and its information added to self._scrapped_videos

        :param video_id: the id of the video
        :returns: recommendations from that video
    """
    self._logger.display()
    print('Getting recommendations for video ' + video_id)
    if video_id in self._scrapped_videos:
        print('Video id ' + video_id + ' is already in the database, reusing it.')
        # This video was seen, returning recommendations that we stored
        return self._scrapped_videos[video_id]['recommendations']


    # Else, we scrap the video:

    url = "https://www.youtube.com/watch?v=" + video_id

    # Until we succeed, try to access the video page:
    while True:
        try:
            html = urlopen(url)
            break
        except Exception as e:
            print(repr(e))
            self._logger.info('We had to wait because an error in scrapping from youtube' + repr(e))
            time.sleep(1)
    self.soup = BeautifulSoup(html, "lxml")

    # Getting views
    views = -1
    likes = -1
    dislikes = -1
    duration = -1
    pubdate = ''
    channel = ''
    channel_id = ''
    recos = []
    title = ''
    keywords = []

    # UPDATED SCRAPPER
    for title_elem in self.soup.findAll('meta', {'name': 'title'}):
      title = title_elem['content']

    for desc_elem in self.soup.findAll('meta', {'name': 'description'}):
      description = desc_elem['content']

    for upload_elem in self.soup.findAll('meta', {'itemprop': 'uploadDate'}):
      pubdate = upload_elem['content']
      now = datetime.datetime.now()
      month_ago = now - dateutil.relativedelta.relativedelta(months=1)
      month_ago_string = month_ago.strftime('%Y-%m-%d')
      if pubdate < month_ago_string and self._skip_older_videos:
        print('*******')
        print('WARNING THE VIDEO ' + video_id + ' WAS PUBLISHED MORE THAN A MONTH AGO, WE ARE SKIPPING IT ' + pubdate)
        print('******* ')
        print('')
        self._logger.info('Channel skipped because it did not publish in a month')
        return []

    for keywords_elem in self.soup.findAll('meta', {'name': 'keywords'}):
      keywords = keywords_elem['content'].split(', ')

    try:
      duration_pattern = re.compile(r'approxDurationMs.....(\d+)')
      duration_text = duration_pattern.findall(repr(self.soup))
      duration = int(int(duration_text[0])/1000)
    except:
      self._logger.info('Scrapping duration not found')
      print('WARNING: scrapping: duration not found')

    pattern = re.compile(r'ytInitialData(.*?\});')
    try:
      v = json.loads(pattern.findall(self.soup.text)[0][5:])
    except:
      try:
        v = json.loads('{"' + pattern.findall(self.soup.text)[0][5:])
      except:
        print('ERROR WITH JSON:')
        print('SKIPPING VIDEO')
        self._logger.warning('Video skipped because badly formated json')
        print(pattern.findall(self.soup.text))
        # print(pattern.findall(self.soup.text)[0][5:])
        print('END ERROR')
        return []

    # print('PATTERN FOUND')
    # for key in v.keys():
    #   print(key + ' :')
    #   if type(v[key]) is str or type(v[key]) is list:
    #     print('    ' + repr(v[key]))
    #   else:
    #     for subkey in v[key].keys():
    #       print('    ' + subkey + ' :')
    #       print('         ' + repr(v[key][subkey]))
    #   print('\n\n\n')

    try:
      recos.append(v['contents']['twoColumnWatchNextResults']['secondaryResults']['secondaryResults']['results'][0]['compactAutoplayRenderer']['contents'][0]['compactVideoRenderer']['videoId'])
    except:
      self._logger.warning('COULD NOT scrap the first recommendation')
      print('WARNING COULD NOT scrap the first recommendation')

    for i in range(1, 20):
      try:
        recos.append(v['contents']['twoColumnWatchNextResults']['secondaryResults']['secondaryResults']['results'][i]['compactVideoRenderer']['videoId'])
      except:
        self._logger.info('One reco could not be found')
        print('DEBUG: one reco could not be found')

    try:
      primary_renderer = self.find_primary_renderer(v)
      try:
        view_text = primary_renderer['viewCount']['videoViewCountRenderer']['viewCount']['simpleText']
        views = self.extract_number_or_default(view_text)
      except:
        self._logger.info('Viewcount not found')
        print('WARNING: viewcount not found in ' + repr(primary_renderer))

      try:
        likes_text = primary_renderer['videoActions']['menuRenderer']['topLevelButtons'][0]['toggleButtonRenderer']['defaultText']['accessibility']['accessibilityData']['label']
        dislikes_text = primary_renderer['videoActions']['menuRenderer']['topLevelButtons'][1]['toggleButtonRenderer']['defaultText']['accessibility']['accessibilityData']['label']
        likes = self.extract_number_or_default(likes_text)
        dislikes = self.extract_number_or_default(dislikes_text)
      except:
        self._logger.info('could not get likes and/or dislikes')
        print('WARNING: could not get likes and/or dislikes')
    except:
      print('ERROR: Primary renderer not found!!')
      self._logger.info('Primary renderer not found, so no likes/dislikes info')

    try:
      channel = v['contents']['twoColumnWatchNextResults']['results']['results']['contents'][1]['videoSecondaryInfoRenderer']['owner']['videoOwnerRenderer']['title']['runs'][0]['text']
    except:
      channel = ''
      print('WARNING channel not found in scrapper')
      self._logger.warning('Channel not found in scrapper')

    try:
      channel_id = v['contents']['twoColumnWatchNextResults']['results']['results']['contents'][1]['videoSecondaryInfoRenderer']['owner']['videoOwnerRenderer']['title']['runs'][0]['navigationEndpoint']['browseEndpoint']['browseId']
    except:
      channel_id = ''
      print('WARNING channel ID not found in scrapper')
      self._logger.warning('Channel ID not found in scrapper')

    if video_id not in self._scrapped_videos:
        self._scrapped_videos[video_id] = {
            'views': views,
            'likes': likes,
            'dislikes': dislikes,
            'recommendations': recos,
            'title': title,
            'id': video_id,
            'channel': channel,
            'pubdate': pubdate,
            'duration': duration,
            'scrapDate': time.strftime('%Y%m%d-%H%M%S'),
            'channel_id': channel_id,
            'description': description,
            'keywords': keywords}
        print('Video scrapped: ' + repr(self._scrapped_videos[video_id]))

    video = self._scrapped_videos[video_id]
    try:
      print(video_id + ': ' + video['title'] + ' [' + channel + ']' + str(video['views']) + ' views and ' + repr(len(video['recommendations'])) + ' recommendations')
    except:
      print('Scrapped vide with special chars ' + video_id)
    return recos

  def find_primary_renderer(self, v):
    for res in v['contents']['twoColumnWatchNextResults']['results']['results']['contents']:
      if 'videoPrimaryInfoRenderer' in res:
        return res['videoPrimaryInfoRenderer']

  def getVideosFromYouTubeAPI(self, video_to_get_by_api):
    """ From a list of YouTube video ids separated by commas, this video will get
        meta data on up to 50 videos, and store it.

        :param video_to_get_by_api: string with video ids comma separated
        :returns: nothing
    """
    
    # API call to YouTube.
    video_infos = self._youtube_client.videos_list_multiple_ids(
        part='snippet,contentDetails,statistics',
        id=video_to_get_by_api)

    # Storing the date of scrapping up to the second
    scrapDate = time.strftime('%Y%m%d-%H%M%S')

    # Converting format and updating the video to channel map
    for video in video_infos['items']:
      video['scrapDate'] = scrapDate
      self._api_videos[video['id']] = video
      self._video_to_chan_map[video['id']] = video['snippet']['channelId']
      if 'snippet' not in video:
        video['snippet'] = {}
      if 'channelTitle' not in video['snippet']:
        video['snippet']['channelTitle'] = ''

      try:
        name = video['snippet']['channelTitle']
        self._channel_id_to_name[video['snippet']['channelId']] = name
      except:
        print('UNKNOWN CHANNEL FOUND FROM API CALL, CHANNEL WAS PROBABLY DELETED')
        self._channel_id_to_name[video['snippet']['channelId']] = 'unknown channel'

      try:
        id_ = video['snippet']['channelId']
        self._channel_name_to_id[video['snippet']['channelTitle']] = id_
      except:
        print('UNKNOWN CHANNEL FOUND FROM API CALL, CHANNEL WAS PROBABLY DELETED')
        self._channel_name_to_id[video['snippet']['channelTitle']] = 'unknown channel'

  def getChannelToCountFromUploads(self, response_list, required_recos):
    """ From a list of uploads of a video from a given channel,
        scraps the number of required videos and update the stats of each channel. 
        (Channels that were the most recommended will be the next to be scrapped)
    """

    channel_to_counts = {}
    videos_to_get = []

    # First pass: looking for videos allready scrapped: we want to get them from cache.
    for video in response_list['items']:
      video_id = video['contentDetails']['videoId']
      if video_id in self._scrapped_videos:
        videos_to_get.append(video_id)

    # How many recommendations did we get from them? Let's see:
    nb_recos = 0
    for video in videos_to_get:
      nb_recos += len(self._scrapped_videos[video]['recommendations'])

    total_video_needed = len(videos_to_get)
    if nb_recos < required_recos:
      total_video_needed = len(videos_to_get) + int((required_recos - nb_recos) / ESTIMATED_RECOS_PER_VIDEO)

    # Second pass: if not enought, adding more videos
    for video in response_list['items']:
      video_id = video['contentDetails']['videoId']
      if video_id not in videos_to_get:
        videos_to_get.append(video_id)
      if len(videos_to_get) >= total_video_needed:
        break

    # For each video that we got, we get its recommendations
    for video_id in videos_to_get:
      self.scrap_the_video(video_id, channel_to_counts)

    return channel_to_counts

  def scrap_the_video(self, video_id, channel_to_counts):
      """ Scraps an individual video.

          :param video_id: string with id of the video.
          :param channel_to_counts: number of times each channel was recommended.
      """
      print('------> Scrapping video '+ video_id)

      # Get recommendations for video id either from scrapping or memory.
      recos = self.get_recommendations(video_id)

      # Now we get all the recommendations. If we don't have info on the video, we need to get some.
      video_to_get_by_api = ''

      for reco in recos:
        if reco not in self._api_videos and reco not in self._video_to_chan_map:
          if video_to_get_by_api != '':
            video_to_get_by_api += ','
          video_to_get_by_api += reco
      if video_to_get_by_api != '':
        self.getVideosFromYouTubeAPI(video_to_get_by_api)

      for reco in recos:
        # Sometimes we are skipping videos that we can't get access to.
        try:
          reco_channel = self.getChannelForVideo(reco)
        except KeyError:
          continue
        channel_to_counts[reco_channel] = channel_to_counts.get(reco_channel, 0) + 1

  def scrap_the_channel(self, channel, required_recos, scrap_only_featuring_channels=None):
    """ Get information on a given channel with YouTube API, and launch scrapping on its videos.
        
        :param channel: the id of the channel
        :param required_recos: how many recommendations we want to have for each video of that channel
        :param scrap_only_featuring_channels: None or a list of channels.
            if not None, we only scrap channels that are featuring one of those    
    """

    print()
    print('Scrapping channel: ' + channel)
    if channel in self._do_not_expand_channel_ids or NO_SCRAPPING:
      return
    self._do_not_expand_channel_ids.add(channel)

    # If we already got the api info for this channel and we want to reuse it, do so
    if channel in self._channel_stats and REUSE_CHANNEL_STATS:
      listResponse = self._channel_stats[channel]['uploads']
    # Otherwise, query it
    else:
      channelResponse = self._youtube_client.channels_list_by_id(
        part='snippet,contentDetails,statistics,brandingSettings',
        id=channel)

      # If the channel has no items, immediatly return
      if channelResponse is None or len(channelResponse['items']) == 0:
        print('NO CHANNEL RESPONSE FOR ' + channel)
        return

      listResponse = self._youtube_client.playlists_list_by_id(
          part='snippet,contentDetails',
          playlistId=channelResponse['items'][0]['contentDetails']['relatedPlaylists']['uploads'],
          maxResults=LATEST_VIDEOS)

      if listResponse is None:
        print('NO CHANNEL LIST FOR ' + channel)
        return


      self._channel_stats[channel] = {
                        'uploads' : listResponse,
                        'statistics' : channelResponse['items'][0].get('statistics', {}),
                        'snippet': channelResponse['items'][0]['snippet'],
                        'featuredChannelsUrls': channelResponse['items'][0].get('brandingSettings', {}).get('channel', {}).get('featuredChannelsUrls', '')}

    # If scrap_only_featuring_channels is not None, check that the new channel
    # features one of the channels in scrap_only_featuring_channels
    if scrap_only_featuring_channels:
      channel_suscribed_not_found = True
      if channel in scrap_only_featuring_channels:
        channel_suscribed_not_found = False
      for c in self._channel_stats[channel]['featuredChannelsUrls']:
          if c in scrap_only_featuring_channels:
            channel_suscribed_not_found = False
      if channel_suscribed_not_found:
        self._do_not_expand_channel_ids.add(channel)
        return

    # Scraps the required videos and update the number of times each channel was recommended.
    channel_to_counts = self.getChannelToCountFromUploads(listResponse, required_recos)

    for channel_recommended in channel_to_counts:
      self._total_channel_stats[channel_recommended] += channel_to_counts[channel_recommended]

  def getChannelsWithEnoughRecos(self):
    """ Returns channels that have more than 50 recommendations. """
    channels_to_recos = {}
    for unused_id, video in self._scrapped_videos.items():
      channels_to_recos[video.get('channel', 'unknown')] = channels_to_recos.get(video.get('channel', 'unknown'), 0) + len(video['recommendations'])
    return channels_to_recos, list(filter(lambda channel: channels_to_recos[channel] > 50, channels_to_recos))

  def printGeneralStats(self):
      """ Print statistics on how many videos and channels were obtained via scrapping
          and API calls
      """
      total_views = 0
      for unused_video_id, video in self._api_videos.items():
        if 'viewCount' in video.get('statistics', {}):
          vc = int(video.get('statistics', {})['viewCount'])
          total_views += vc

      print('\n\n\n\n\n\n')
      print(' Number of api videos: ' + repr(len(self._api_videos)) + ' total views ' + repr(total_views))
      channels_to_recos, channels_with_enough_recos = self.getChannelsWithEnoughRecos()
      print(' Number of scrapped videos: ' + repr(len(self._scrapped_videos)) + ' total channels ' + repr(len(channels_to_recos)) + ' which have more than 50 recos ' + repr(len(channels_with_enough_recos)))
      return channels_with_enough_recos

  def add_channels_from_searches(self, searches, channels):
    """ Perform search API call for different searches

        :param searches: array with search queries
        :param channels: list were the new channels will be appended
    """
    if searches == []:
      return
    all_searches = {}
    for search in searches:
      list_of_results = self._youtube_client.search_list_by_keyword(
        part='snippet',
        maxResults=50,
        q=search,
        type='video')

      all_searches[search] = list_of_results

      for result in list_of_results['items']:
        chan_id = result['snippet']['channelId']
        if chan_id not in channels:
          channels.append(chan_id)
          print('Adding channel ' + chan_id)
        self.scrap_the_video(result['id']['videoId'], {})

    self.saveToFile(all_searches, DATA_DIRECTORY+ self._folder + '/' + self._folder + '-searches')

  def get_all_api_data(self):
    """ For all videos that were scrapped, get more information with the YouTube API. """
    
    # First we try all the API data present in channel-stats
    video_to_get_by_api = ''
    video_to_get_by_api_nb = 0
    total_videos_got = 0

    for video in self._scrapped_videos:
      if video not in self._api_videos:
        # YouTube API takes 50 videos max.
        if video_to_get_by_api_nb == 50:
          print('Calling YouTube API to collect info about 50 videos...')
          self.getVideosFromYouTubeAPI(video_to_get_by_api)
          video_to_get_by_api = ''
          video_to_get_by_api_nb = 0
        if video_to_get_by_api != '':
          video_to_get_by_api += ','
        video_to_get_by_api += video
        video_to_get_by_api_nb += 1
        total_videos_got += 1

      for reco in self._scrapped_videos[video]['recommendations']:
        if total_videos_got % 1000 == 0 and total_videos_got > 0:
          self.saveToFile(self._video_to_chan_map, self._video_to_chan_file)
          print('Video to chan made with length ' + repr(len(self._video_to_chan_map)))
          total_videos_got += 1

        if reco not in self._api_videos:
          if video_to_get_by_api_nb == 50:
            self.getVideosFromYouTubeAPI(video_to_get_by_api)
            video_to_get_by_api = ''
            video_to_get_by_api_nb = 0

          if video_to_get_by_api != '':
            video_to_get_by_api += ','
          video_to_get_by_api += reco
          video_to_get_by_api_nb += 1
          total_videos_got += 1
  
    if video_to_get_by_api != '':
      self.getVideosFromYouTubeAPI(video_to_get_by_api)

    for video in self._api_videos:
      self._video_to_chan_map[video] = self._api_videos[video]['snippet']['channelId']
    self.saveToFile(self._video_to_chan_map, self._video_to_chan_file)
    print('New video to chan made with length ' + repr(len(self._video_to_chan_map)))

  def write_channel_stats(self):
    print('Writing channel stats...')
    chanid_to_recos = collections.defaultdict(int)
    chanid_to_name = {}
    reco_chan_not_found = 0
    for video in self._scrapped_videos:
      for reco in self._scrapped_videos[video]['recommendations']:
        if reco in self._api_videos:
          chan = self._api_videos[reco]['snippet']['channelId']
          chanid_to_name[self._api_videos[reco]['snippet']['channelId']] = self._api_videos[reco]['snippet']['channelTitle']
        elif reco in self._scrapped_videos:
          chan = self._scrapped_videos[reco]['channelId']
          chanid_to_name[self._scrapped_videos[reco]['channelId']] = self._scrapped_videos[reco]['channel']
          if chan == '' or self._scrapped_videos[reco]['channel'] == '':
            reco_chan_not_found += 1
            print('WARNING CHANNEL WAS NOT FOUND FOR VIDEO ' + video)
            continue
        else:
          reco_chan_not_found += 1
          continue
        chanid_to_recos[chan] += 1

    total_recos = 0
    for cid in chanid_to_recos:
      total_recos += chanid_to_recos[cid]

    print('Total Recos: ' + str(total_recos) + ' - reco chan not found ' + str(reco_chan_not_found))

    names = []
    pc_recos = []
    recos_small_chans = 0

    for cid in sorted(chanid_to_recos, key=chanid_to_recos.get, reverse=True):
      percent = readable_percent(chanid_to_recos[cid] / total_recos)
      if len(names) < 25:
        names.append(chanid_to_name[cid])
        pc_recos.append(percent)
      else:
        recos_small_chans += chanid_to_recos[cid]

    channels = {'names': names, 'percent_recos': pc_recos, 'percent_other_channels': readable_percent(recos_small_chans/total_recos)}
    self.saveToFile(channels, DATA_DIRECTORY + self._folder + '-chans')

  def write_result_file(self):
    """ Write file with videos that were recommended the most. """

    print('RESULTS')
    # 1 compute number of recos per video
    videos_to_recos = collections.defaultdict(int)
    videos_to_chans_recommending = collections.defaultdict(set)
    total_recommendations = 0
    for video in self._scrapped_videos:
      for reco in self._scrapped_videos[video]['recommendations']:
        total_recommendations += 1
        videos_to_recos[reco] += 1
        if video in self._api_videos:
          # Adding Channel title, video id, video title
          videos_to_chans_recommending[reco].add(
            (self._api_videos[video]['snippet']['channelTitle'], video, self._api_videos[video]['snippet']['title']))

    chaname_to_subs = collections.defaultdict(int)
    for c in self._channel_stats:
      chaname_to_subs[self._channel_stats[c]['snippet']['title']] = int(self._channel_stats[c].get('statistics', {}).get('subscriberCount',0))

    sorted_channel_names = sorted(chaname_to_subs, key=chaname_to_subs.get, reverse=True)
    nb_ok_vids = 0
    nb_not_ok_vids = 0
    final_dict = {'info_channels': []}
    for video in sorted(videos_to_recos, key=videos_to_recos.get, reverse=True):

      # We only save videos that have been viewed more than once in order to save space.
      if videos_to_recos[video] > 1:
        channels_recommending = []

        # Now we get all the channels that recommend this video in the order 
        for cn in sorted_channel_names:
          for ct in videos_to_chans_recommending[video]:
            if cn == ct[0]:
              channels_recommending.append([ct[0], ct[1], ct[2]])
          if len(videos_to_chans_recommending[video]) == len(channels_recommending):
            break
   
        if video in self._api_videos:
          nb_ok_vids += 1
          video_info = self._api_videos[video]
          final_dict['info_channels'].append({
            "id": video,
            'pdate': video_info.get('snippet', {}).get('publishedAt', ''),
            "views": video_info.get('statistics', {}).get('viewCount', -1),
            "dislikes": video_info.get('statistics', {}).get('dislikeCount', -1),
            "likes": video_info.get('statistics', {}).get('likeCount', -1),
            "views": video_info.get('statistics', {}).get('viewCount', -1),
            "nb_recommendations": videos_to_recos[video],
            "title": video_info['snippet']['title'],
            "channel":  video_info['snippet']['channelTitle'],
            "comments": int(video_info.get('statistics', {}).get('commentCount', 0)),
            "from_chans": channels_recommending
          })
        # If the video is only in scrapped videos:
        elif video in self._scrapped_videos:
          nb_ok_vids += 1
          video_info = self._scrapped_videos[video]
          final_dict['info_channels'].append({
            "id": video,
            'pdate': video_info['pubdate'],
            "views": video_info['views'],
            "dislikes": video_info['dislikes'],
            "likes": video_info['likes'],
            "views": video_info['views'],
            "nb_recommendations": videos_to_recos[video],
            "title": video_info['title'],
            "channel": video_info['channel'],
            "from_chans": channels_recommending
          })
        else:
          nb_not_ok_vids += 1
          print('WARNING: one video not in API VIDEOS will be ignored despite beeing recommended ' + str(videos_to_recos[video]) + ' out of ' + str(nb_ok_vids))

    final_dict['total_videos_recommended'] = len(videos_to_recos)
    final_dict['total_recommendations'] = total_recommendations
    print(' Videos with info ok ' + repr(nb_ok_vids) + ' not ok ' + repr(nb_not_ok_vids))
    self.saveToFile(final_dict, DATA_DIRECTORY + self._folder)
    print('Result file written! ')

  def describe_channels(self, only_not_in_base_channels=False, base_channels=None):
    """ Print the 500 top channels by recommendations. """

    if only_not_in_base_channels:
      print()
      print('Printing top performing channels that are not in base channels')
    else:
      print()
      print('Printing top performing channels')

    # Computing number of recommendations
    total_channel_stats = collections.defaultdict(int)
    for unused_vid, info in self._scrapped_videos.items():
      for reco in info['recommendations']:
        total_channel_stats[self._video_to_chan_map.get(reco, 'unknown')] += 1

    total_printed = 0
    for chan in sorted(total_channel_stats, key=total_channel_stats.get, reverse=True):
      if total_printed > 500:
        break

      if total_channel_stats[chan] < 2:
        break

      if only_not_in_base_channels and chan in base_channels:
        continue

      try:
        print(self._channel_stats[chan]['snippet']['title'] + ' '  + chan + '  ' + str(total_channel_stats[chan]))
      except:
        print(str(total_channel_stats[chan]) + ' ' + chan)
      total_printed += 1

  def scrap_from_base(self, base_channels, max_channels, required_recos, only_scrap_chans_featuring_base=False):
    """ This function start the snowball mechanism from a base of channels.
    
        :param base_channels: a list of channel ids to start from
        :param max_channels: the max amount of channels we want to get to
        :param required_recos: how many recommendations per channel we want
        :only_scrap_chans_featuring_base: if true, we'll only expand channels
            that feature one of the base channels
    """

    suscribed_to = None
    if only_scrap_chans_featuring_base:
      suscribed_to = base_channels

    number_of_saved_videos = len(self._api_videos)

    # Scrapping the base channels
    for channel in base_channels:
      self.scrap_the_channel(channel, required_recos)
      if len(self._api_videos) > number_of_saved_videos + 100:
        self.save_videos()
        number_of_saved_videos = len(self._api_videos)

    # Saving all stats, in case the program is interupted.
    self.save_videos()

    # Load the list of channels not to be expanded
    nb_channels_not_to_scrap = 0
    with open('blacklisted_youtube_channels.txt', 'r', encoding='utf-8') as infile:
      for line in infile:
        nb_channels_not_to_scrap += 1
        self._do_not_expand_channel_ids.add(line.strip())
    print(str(nb_channels_not_to_scrap) + ' channels were added from youtube_channels_not_info.txt to not be scrapped')

    # We snowball here for extra channels.
    nb_extra_channels = 0
    while nb_extra_channels + len(base_channels) < max_channels:
      nb_extra_channels += 1
      print('\n\n\n')
      print('Sorting channels ... ')
      sorted_top_channels = sorted(self._total_channel_stats, key=self._total_channel_stats.get, reverse=True)
      print('Sorted. Getting channels with enough recos ... ')
      unused_v, channels_with_enough_recos = self.getChannelsWithEnoughRecos()
      print('Done. Checking that all channels have been scrapped:')
      for channel in sorted_top_channels[0:nb_extra_channels]:
        if channel not in channels_with_enough_recos and channel not in self._do_not_expand_channel_ids:
          self.scrap_the_channel(channel, required_recos, scrap_only_featuring_channels=suscribed_to)

      # Display some of the channels were most recommended 
      print('\n\n\n')
      print('General Stats after computing ' + repr(nb_extra_channels) + ' channels')
      for channel in sorted_top_channels[0:20]:
        try:
          print('   - ' + channel + '( ' + self._channel_id_to_name[channel] + ' ) - ( ' + repr(self._total_channel_stats[channel]) + ' )' )
        except:
          print('- Channel that we will discover or that we do not care about -')

      print('...')
      for channel in sorted_top_channels[nb_extra_channels-2:nb_extra_channels + 2]:
        try:
          print('   - ' + channel + '( ' + self._channel_id_to_name[channel] + ' ) - ( ' + repr(self._total_channel_stats[channel]) + ' )' )
        except:
          print('- Channel that we will discover or that we do not care about -')

      # Saving if we have enough new videos.
      if len(self._api_videos) > number_of_saved_videos + 100:
        self.save_videos()
        number_of_saved_videos = len(self._api_videos)

    # Final saving all statistics
    self.get_all_api_data()
    self.save_videos()
    self.write_result_file()
    self.write_channel_stats()


def compute_recent_files(base_domain, original_channels, max_dates=31):
  """ Compute the files that are used

      :param base_domain: filename base
      :param original channels: the base channels that were used
      :param max_dates: the number of dates that will be loaded in memory
            if it is too big, reduce it, but you won't be able to obtain the bigger files.
  """

  if base_domain == 'france-':
    file_name = 'france-info-'
  else:
    file_name = base_domain

  filenames = os.listdir(DATA_DIRECTORY)

  # Swapping dates from dd-mm-yyyy format to yyyy-mm-dd
  def invert_date(date):
      return date[6:10] + '-' + date[3:5] + '-' + date[0:2]

  # Swapping dates from yyyy-mm-dd format to dd-mm-yyyy
  def revert_date(date):
      return date[8:10] + '-' + date[5:7] + '-' + date[0:4]

  def compute_recommendations_from_base(base_domain, base_channels, scrapped_videos, api_videos, specific_date):
    print(' Computing recommendations from base: ' + base_domain + ' and date ' + specific_date)
    base_channel_recos_this_day = collections.defaultdict(int)
    base_channel_recos_views_this_day = collections.defaultdict(int)
    for v in scrapped_videos[specific_date]:
        if v in api_videos[specific_date] and api_videos[specific_date][v]['snippet']['channelId'] in base_channels:
            for r in scrapped_videos[specific_date][v]['recommendations']:
                base_channel_recos_this_day[r] += 1
                base_channel_recos_views_this_day[r] += int(api_videos[specific_date][v]['statistics'].get('viewCount', 100))

    with open(base_domain + 'base_recos_' + specific_date + '.json', 'w') as fp:
        json.dump(dict(base_channel_recos_this_day), fp)
    with open(base_domain + 'base_recoviews_' + specific_date + '.json', 'w') as fp:
        json.dump(dict(base_channel_recos_views_this_day), fp)
  
  # Sort dates
  good_name_size = len(base_domain + '14-10-2018')
  dates_set = set()
  for filename in filenames:
      if '.json' not in filename and base_domain in filename and len(filename) == good_name_size:
          dates_set.add(filename.replace(base_domain,''))
  rdates_set = set(map(invert_date, dates_set))
  dates= list(map(revert_date, sorted(rdates_set, reverse=True)))[0:max_dates]

  def makeFolder(date):
      """ Creates folder name 

          :param date: date of the scrapping
          :returns: folder name
      """
      return base_domain + date

  folders = list(map(makeFolder, dates))
  print(folders)

  def loadFromFile(filename):
    """ Loads a dictionary from a file, and returns an empty dictionary if file
        is not there.

        :param filename: the filename to load
        :returns: dictionary, or empty dict if no file 
    """
    print('Loading ' + filename + ' ...')
    try:
      with open(filename, "r") as json_file:
        my_dict = json.load(json_file)
    except Exception as e:
      print(e)
      my_dict = {}
    return my_dict
    print(filename + ' loaded!')

  v_to_channame = {}

  # Loading files
  channel_stats = {}
  scrapped_videos = collections.defaultdict(dict)
  all_scrapped_vids = set()
  for date in dates:
      folder = makeFolder(date)
      channel_stats_loc = loadFromFile(DATA_DIRECTORY + folder + '/all_channels.json')
      for chan in channel_stats_loc:
          if chan not in channel_stats:
              channel_stats[chan] = channel_stats_loc[chan]
      scrapped_videos[date] = loadFromFile(DATA_DIRECTORY + folder + '/scrapped_videos.json')
      for vid in scrapped_videos[date]:
          all_scrapped_vids.add(vid)
          v_to_channame[vid] = scrapped_videos[date][vid]['channel']

  VIDEO_TO_CHAN_FILE = DATA_DIRECTORY + 'video_to_chan.json'

  api_videos = {}
  api_videos_date = collections.defaultdict(dict)

  for date in dates:
      folder = makeFolder(date)
      api_videos_date[date] = loadFromFile(DATA_DIRECTORY + folder + '/api_videos.json')
      api_videos.update(api_videos_date[date])
      for vid in api_videos_date[date]:
          v_to_channame[vid] = api_videos_date[date][vid]['snippet']['channelTitle']

  compute_recommendations_from_base(base_domain, original_channels, scrapped_videos, api_videos_date, dates[0])

  all_videos = set(all_scrapped_vids).union(set(api_videos.keys()))

  # Delete scrapped videos that returned empty data.
  for date in dates:
      for v in list(scrapped_videos[date].keys()):
          if scrapped_videos[date][v]['title'] == '':
              del scrapped_videos[date][v]

  # Computing the number of videos to recommendations.
  video_to_recos = collections.defaultdict(int)
  video_to_recos_date = {}
  rdates = list(reversed(dates))

  print(len(all_scrapped_vids))

  # Computing the number of estimated recommendations for all videos.
  for v in all_scrapped_vids:
      first_index = 0
      while (first_index < len(rdates) - 1):
          # Find first date
          if v not in scrapped_videos[rdates[first_index]]:
              first_index +=1
              continue
          second_index = first_index + 1
          while True:
              # Couldn't find a second date. Let's break both loops.
              if second_index >= len(rdates):
                  first_index = len(rdates)
                  break
              
              if v not in scrapped_videos[rdates[second_index]]:
                  second_index +=1
                  continue

              # Video is both in first date and second date
              # We look at all recommendations at first date.
              # If the video is also recommended at the second date,
              # we assume that the video has been recommended in between the two dates
              # so the number of recommendations for this video is the increase of view of the original video
              first_video = scrapped_videos[rdates[first_index]][v]
              second_video = scrapped_videos[rdates[second_index]][v]

              view_inc = second_video['views'] - first_video['views']

              if view_inc > 0:
                  for reco in first_video['recommendations']:
                      if reco in second_video['recommendations']:
                          # We approximate that only half of the recos were from that video
                          video_to_recos[reco] += int(view_inc)
                          nb_index_to_retribute = int(1 + second_index - first_index)
                          for delta in range(nb_index_to_retribute):
                              if reco not in video_to_recos_date:
                                  video_to_recos_date[reco] = collections.defaultdict(int)
                              video_to_recos_date[reco][rdates[first_index + delta]] += int(view_inc/(nb_index_to_retribute))

              first_index = second_index
              break

  # Computing information to display about each video
  # Which channels were recommending a given video            
  video_to_chans = collections.defaultdict(set)
  # Which channels were recommending a given video, per date 
  video_date_to_chans = collections.defaultdict(dict)
  # Maximum number of channel recommending the video
  video_to_max_chans = collections.defaultdict(int)
  # For each chan title, number of time each other chan is recommended
  chans_title_to_chan_to_recos = {}
  total_recos = 0
  for date in dates:
      for v in scrapped_videos[date]:
          total_recos += len(scrapped_videos[date][v]['recommendations'])
          for reco in scrapped_videos[date][v]['recommendations']:
              video_to_chans[reco].add(scrapped_videos[date][v]['channel'])
              if reco not in video_date_to_chans[date]:
                  video_date_to_chans[date][reco] = set()
              video_date_to_chans[date][reco].add(scrapped_videos[date][v]['channel'])
              if reco in api_videos:
                  reco_chan = api_videos[reco]['snippet']['channelTitle']
                  if reco_chan not in chans_title_to_chan_to_recos:
                      chans_title_to_chan_to_recos[reco_chan] = collections.defaultdict(int)
                  chans_title_to_chan_to_recos[reco_chan][scrapped_videos[date][v]['channel']] +=1

  video_to_chans_length = {}
  for v in video_to_chans:
      video_to_chans_length[v] = len(video_to_chans[v])
      
  for v in video_to_chans:
      for date in video_date_to_chans:
          if v in video_date_to_chans[date] and len(video_date_to_chans[date][v]) > video_to_max_chans[v]:
              video_to_max_chans[v] = len(video_date_to_chans[date][v])

  # Computing channel stats
  chaname_to_subs = {}
  for c in channel_stats: 
      chaname_to_subs[channel_stats[c]['snippet']['title']] = int(channel_stats[c].get('statistics', {}).get('subscriberCount',0))
  
  # Computing channel names
  original_channels_names = set()
  for c in original_channels:
    original_channels_names.add(channel_stats.get(c, {}).get('snippet', {}).get('title', 'Unknown channel name'))

  def getSortedChannels(chanset):
    """ Get the sorted list of channels that have more than 100000 subscribers.
    
        :param chanset: a set of channels
        :returns: list of channel titles
    """
    chanmap = {chan: chaname_to_subs.get(chan, 0) for chan in chanset if chaname_to_subs.get(chan, 0) > 100000}
    return sorted(chanmap, key=chanmap.get, reverse=True)

  # Returns dates in chronological order
  ordered_dates = list(map(invert_date, reversed(dates)))

  def make_video_history(v, days=60, view_increase=None, include_history=False):
      """  For a video, build the history of views, likes, etc...

          :param days: number of days considered
          :view_increase: dictionary with video to view increase
          :include history: if we want to include the view history in the output
          :returns: a dict with all interesting data for that video
      """
      dates_considered = dates[::-1][-days:]
      max_views = max(int(api_videos_date[date].get(v, {'statistics':{'viewCount':0}}).get('statistics', {}).get('viewCount', -1)) for date in dates)
      max_likes = max(int(api_videos_date[date].get(v, {'statistics':{'likeCount':0}}).get('statistics', {}).get('likeCount', 0)) for date in dates)
      max_dislikes = max(int(api_videos_date[date].get(v, {'statistics':{'dislikeCount':0}}).get('statistics', {}).get('dislikeCount', 0)) for date in dates)
      video_data = {
          'title': api_videos[v]['snippet']['title'],
          'pdate': api_videos[v].get('snippet', {}).get('publishedAt', ''),
          'id': v,
          'views': max_views,
          'likes': max_likes,
          'dislikes': max_dislikes,
          'top_chans_rec': getSortedChannels(video_to_chans[v]),
          'nb_chans_rec': len(video_to_chans[v]),
          'observed_recos': video_to_recos[v],
          'channel': api_videos[v]['snippet']['channelTitle'],
          'comments': int(api_videos[v].get('statistics', {}).get('commentCount', 0))
      }
      if include_history:
          chan_history = [len(video_date_to_chans[date].get(v,[])) for date in dates_considered]
          view_history = [api_videos_date[date].get(v, {'statistics':{'viewCount':-1}}).get('statistics', {}).get('viewCount', -1)  for date in dates_considered]
          like_history = [api_videos_date[date].get(v, {'statistics':{'likeCount':-1}}).get('statistics', {}).get('likeCount', -1)  for date in dates_considered]   
          dislike_history = [api_videos_date[date].get(v, {'statistics':{'dislikeCount':-1}}).get('statistics', {}).get('dislikeCount', -1)  for date in dates_considered]
          reco_history = [video_to_recos_date[v].get(date, 0) for date in dates_considered] if v in video_to_recos_date else []
          video_data['chan_history'] = chan_history
          video_data['view_history'] = view_history
          video_data['like_history'] = like_history
          video_data['dislike_history'] = dislike_history
          video_data['reco_history'] = reco_history

      if view_increase and view_increase > 1000 and video_data['observed_recos']/view_increase > 10000:
        print(' WEIRDLY RECOMMENDED VIDEO' )
        print(video_data)

      if view_increase:
        video_data['view_inc'] = view_increase
      return video_data

  def compute_recent_recos(dayz):
    """  Computes the number of recommendations for a specific number of days """
    video_to_recent_recos = {}
    for v in all_videos:
      reco_chans = set()
      for date in dates[0:dayz]:
        reco_chans.update(video_date_to_chans[date].get(v,[]))
      video_to_recent_recos[v] = len(reco_chans)
    return video_to_recent_recos

  def compute_scrapped_channels(dayz):
    """  Computes the channels from which videos have been scrapped."""
    scrap_chans = set()
    for date in dates[0:dayz]:
      for v in scrapped_videos[date]:
        cid = scrapped_videos[date][v].get('channel_id', 'UNKNONW CHANNEL')
        cn = scrapped_videos[date][v].get('channel', 'UNKNONW CHANNEL')
        if cid != 'UNKNONW CHANNEL':
          scrap_chans.add(cid)
        if cn != 'UNKNONW CHANNEL':
          scrap_chans.add(cn)
    return scrap_chans

  def compute_recent_views(dayz):
    """ Computes the view increase over the last n dayz. """
    recent_views = {}
    missing = 0
    for v in all_videos:
      view_history = [int(api_videos_date[date].get(v, {'statistics':{'viewCount': scrapped_videos[date].get(v, {'views': -1})['views']}}).get('statistics', {}).get('viewCount', -1))  for date in reversed(dates)]   
      if v not in api_videos:
        missing += 1
        continue
      pub_date = api_videos[v]['snippet'].get('publishedAt', '')[0:10]

      if pub_date == '':
        missing += 1
        continue
      # If the video was published during the period, the increase is the max view
      if pub_date >= invert_date(dates[dayz]):
          tmp_views = max(view_history[-dayz:])
          if tmp_views > 0:
              recent_views[v] = tmp_views
      else:
          # If the video was published before
          maxi = max(view_history[-dayz:])
          if maxi <=0:
              continue
          mini = min(x for x in view_history[-dayz:] if x > 0)
          recent_views[v] = maxi - mini

    print('Number of missing videos in api: ' + repr(missing))
    return recent_views

  # A channel name that is impossible
  impossible_chan_name = '1234dfs5678fsd9009fsdhfewirioffdsdf'
  def snippetIsInSet(snippet, chans):
    """ Checks if a snippet is in a list of channels, that could be channel name or channel ids """
    if snippet.get('channelName', impossible_chan_name) in chans or snippet['channelId'] in chans:
      return True
    return False

  def write_xls_video_file(final_vids, file_base_name, recent_views, nb_dates):
    """ Write a xlsx file for this data

        :param final_vids: the list of videos
        :file_base_name: the base of the filename
        :recent_views: a dict video to number of recent views
        :nb_dates: the number of dates that were considered
    """

    xls_file = xlsxwriter.Workbook(file_base_name + '.xlsx')
    bold = xls_file.add_format({'bold': True})

    worksheet = xls_file.add_worksheet('Videos')
    view_format = xls_file.add_format({'num_format': '###,###,###,###'})
    worksheet.set_column('A:A', 50)
    worksheet.set_column('B:B', 50)
    worksheet.set_column('C:C', 50)
    worksheet.set_column('D:D', 30)
    worksheet.set_column('E:E', 30)
    worksheet.set_column('F:F', 30)
    worksheet.set_column('G:G', 20)
    worksheet.set_column('H:H', 20)
    worksheet.set_column('I:I', 20)
    worksheet.set_column('J:J', 20)
    worksheet.set_column('K:K', 20)
    worksheet.set_column('L:L', 200)

    worksheet.write('A1', 'URL', bold)
    worksheet.write('B1', 'Title', bold)
    worksheet.write('C1', 'Channel', bold)
    worksheet.write('D1', 'Upload date', bold)
    worksheet.write('E1', 'Views', bold)
    worksheet.write('F1', 'Likes', bold)
    worksheet.write('G1', 'Dislikes', bold)
    worksheet.write('H1', 'Number of channels recommending it', bold)
    worksheet.write('I1', 'Minimum observed recommendations', bold)
    worksheet.write('J1', 'Comments', bold)
    worksheet.write('K1', 'Views in last ' + repr(nb_dates)  + ' days' , bold)
    worksheet.write('L1', 'Top Channels Recommending It with > 100k subs', bold)

    i=2
    for vid in final_vids:
        title = vid['title']
        chan = vid['channel']
        upload = vid['pdate']
        url= 'https://www.youtube.com/watch?v=' + vid['id']
        views = vid['views']
        likes = vid['likes']
        dislikes = vid['dislikes']
        nb_chans_rec = vid['nb_chans_rec']
        observed_recommendations = vid['observed_recos']
        comments = vid['comments']
        top_chans_rec = repr(vid['top_chans_rec'])

        worksheet.write(i, 0, url)
        worksheet.write(i, 1, title)
        worksheet.write(i, 2, chan)
        worksheet.write(i, 3, upload)
        worksheet.write(i, 4, views, view_format)
        worksheet.write(i, 5, likes, view_format)
        worksheet.write(i, 6, dislikes, view_format)
        worksheet.write(i, 7, nb_chans_rec, view_format)
        worksheet.write(i, 9, observed_recommendations)
        worksheet.write(i, 10, comments)
        worksheet.write(i, 11, recent_views.get(vid['id'], 'unknown'))
        worksheet.write(i, 11, top_chans_rec)
        i+=1

    chan_counts = collections.defaultdict(int)

    for vid in final_vids:
        chan = vid['channel']
        chan_counts[chan] += recent_views.get(vid['id'], 0)

    worksheet = xls_file.add_worksheet('Channel Recent Views')

    worksheet.set_column('A:A', 50)
    worksheet.set_column('B:B', 70)
    worksheet.write('A1', 'Channel', bold)
    worksheet.write('B1', 'Views on this channel for ' + repr(nb_dates) + ' days', bold)

    i = 0
    for c in sorted(chan_counts, key=chan_counts.get, reverse=True):
        worksheet.write(i, 0, c)
        worksheet.write(i, 1, chan_counts[c])
        i += 1
    xls_file.close()

  def compute_evolution_file(file_id, nb_dates, videos_included, videos_with_history=100, original_channels_only=False):
    """ Create the file with the historical evolution of number of views and channel recommending a video.
    
        :param file_id: the id of the file
        :param videos_included: number of videos that should be in the file
        :param videos_with_history: number of videos that should be stored with history
        :returns: nothing
    """

    # If max_dates is not big enough, we do not compute it
    if max_dates >= nb_dates:
      scrapped_channels = compute_scrapped_channels(nb_dates)
      recent_views = compute_recent_views(nb_dates)
      i=0
      final_vids = []
      print(len(recent_views))

      # Computing the video file sorted by recent views
      for v in sorted(recent_views, key=recent_views.get, reverse=True):
          if (i < videos_included and v in api_videos and snippetIsInSet(api_videos[v]['snippet'], scrapped_channels) and
              # If we only want original channels :
            (api_videos[v]['snippet']['channelId'] in original_channels or not original_channels_only)):
              final_vids.append(make_video_history(v, days=nb_dates, view_increase=recent_views[v], include_history=(i<videos_with_history)))
              i+=1
      file_base_name = 'evo-' +  file_name + file_id + '-v'
      with open(file_base_name + '.json', 'w') as fp:
          json.dump({"videos": final_vids, 'dates': ordered_dates[-nb_dates:]}, fp)
      write_xls_video_file(final_vids, file_base_name, recent_views, nb_dates)

      # Computing the channel file sorted by recent views
      i=0
      final_vids = []
      recent_channel_views = collections.defaultdict(int)
      for v in sorted(recent_views, key=recent_views.get, reverse=True):
        recent_channel_views[v_to_channame.get(v, '')] += recent_views[v]
      for c in sorted(recent_channel_views, key=recent_channel_views.get, reverse=True):
        if (c != '' and recent_channel_views[c] > 1000 and
            (c in original_channels_names or not original_channels_only)):
            final_vids.append({'view_inc': recent_channel_views[c], 'title': c})
      file_base_name = 'evo-' +  file_name + file_id + '-c'
      with open( file_base_name + '.json', 'w') as fp:
          json.dump({"videos": final_vids, 'dates': ordered_dates[-3:]}, fp)

      # Computing the video file sorted by recommendations
      recent_recos = compute_recent_recos(nb_dates)
      i=0
      final_vids = []
      print(len(recent_views))
      for v in sorted(recent_recos, key=recent_recos.get, reverse=True):
          if i < videos_included and v in api_videos and snippetIsInSet(api_videos[v]['snippet'], scrapped_channels):
              final_vids.append(make_video_history(v, days=3, view_increase=recent_views.get(v, 0), include_history=(i<videos_with_history)))
              i+=1
      file_base_name = 'evo-' +  file_name + file_id + '-r'
      with open(file_base_name + '.json', 'w') as fp:
          json.dump({"videos": final_vids, 'dates': ordered_dates[-3:]}, fp)
      write_xls_video_file(final_vids, file_base_name, recent_views, nb_dates)

  # For the france dataset, we only want channels from the base
  if 'france' in base_domain.lower():
    original_channels_only = True
  else:
    original_channels_only = False

  compute_evolution_file('3days', 3, 5000, videos_with_history=100, original_channels_only=original_channels_only)
  compute_evolution_file('week', 7, 4000, videos_with_history=100, original_channels_only=original_channels_only)
  compute_evolution_file('month', 30, 3000, videos_with_history=100, original_channels_only=original_channels_only)

def loadOrFail(filename):
    """ Try to load a file, and returns an exception if it is not found.

        :param filename: the file that we try to load
    """

    print('Loading ' + filename + ' ...')
    with open(filename + '.json', "r") as json_file:
        obj = json.load(json_file)
    return obj

def printHTML(video_id):
  print(
    """
      <hl>
      https://www.youtube.com/watch?v=%s
      <br>
      <iframe allowFullScreen="allowFullScreen" src="https://www.youtube.com/embed/%s?ecver=1&amp;iv_load_policy=1&amp;yt:stretch=16:9&amp;autohide=1&amp;color=red&amp;width=560&amp;width=560" width="560" height="315" allowtransparency="true" frameborder="0"><div><a rel="" id="ryCClBfn" href="https://www.nhsdiscounts.org.uk">https://www.nhsdiscount.org.uk</a></div><div><a rel="" id="ryCClBfn" href="https://www.ihertfordshire.co.uk/preparing-for-lockdown-2-0-in-hertfordshire/">working from home</a></div><script type="text/javascript">function execute_YTvideo(){return youtube.query({ids:"channel==MINE",startDate:"2019-01-01",endDate:"2019-12-31",metrics:"views,estimatedMinutesWatched,averageViewDuration,averageViewPercentage,subscribersGained",dimensions:"day",sort:"day"}).then(function(e){},function(e){console.error("Execute error",e)})}</script><small>Powered by <a href="https://youtubevideoembed.com/ ">Embed YouTube Video</a></small></iframe>
    """%(video_id,video_id)
  )

def main():
  while True:
    launch_scrapping()
    print('Scrapping done, next in 23 hours')
    time.sleep(81200)

def launch_scrapping():
  global parser
  # Reading command line arguments
  parser = argparse.ArgumentParser(description=__doc__)
  parser.add_argument('--set', help='The starting set of channels')
  parser.add_argument('--date', help='The date from which the s crapping is done, in format dd-mm-yyyy')
  parser.add_argument('--noscrap', help='Skip scrapping')
  parser.add_argument('--onlycomputerecent', help='If we only compute recent files')
  parser.add_argument('--channelstats', help='If we only compute channel stats')

  args = parser.parse_args()

  #print('Sleeping 2 hours')
  #time.sleep(6200)

  # Setting different parameters for different datasets
  only_scrap_chans_featuring_base = False
  required_recos = REQUIRED_RECOS

  exclude_channels = []
  skip_older_videos = True

  # Set default to US
  if args.set is None:
    args.set = 'us'

  if args.set.lower() == 'us':
    os.system('say est ce que le vpn est bien aux zu s a?')
    base_channels = loadOrFail('base_channels/us_information_channels')
    base_domain = 'us-info-'
    searches_to_add = []
    max_chans = 1200
    nb_dates = 31
    exclude_channels = ['UCsT0YIqwnpJCM-mx7-gSA4Q', # TEDx Talks
                        'UCJFp8uSYCjXOMnkUyb3CQ3Q', # Tasty
                        'UC6E2mP01ZLH_kbAyeazCNdg', # Brave Wilderness
                        'UC4PooiX37Pld1T8J5SYT-SQ', # Good Mythical Morning
                        'UCAuUUnT6oDeKwE6v1NGQxug', # TED
                        'UCIEv3lZ_tNXHzL3ox-_uUGQ', # Gordon Ramsay
                        'UCpVm7bg6pXKo1Pr6k5kxG9A', # National Geographic
                        'UCdxi8d8qRsRyUi2ERYjYb-w', # TheRichest
                        'UCd1fLoVFooPeWqCEYVUJZqg', # Matt Stonie
                        'UCay_OLhWtf9iklq8zg_or0g', # As/Is
                        'UCsooa4yRKGN_zEE8iknghZA', # TED-Ed
                        'UCX6b17PVsYBQ0ip5gyeme-Q', # CrashCourse
                        'UCBUVGPsJzc1U8SECMgBaMFw', # BuzzFeed Multiplayer
                        'UCGt7X90Au6BV8rf49BiM6Dg', # Ray William Johnson  -  9690000
                        'UCBvc7pmUp9wiZIFOXEp1sCg', # DemolitionRanch  -  8590000
                        'UCSpVHeDGr9UbREhRca0qwsA', # Howcast  -  8150000
                        'UCkQO3QsgTpNTsOw6ujimT5Q', # BE AMAZED  -  6740000
                        'UCBINYCmwE29fBXCpUI8DgTA', # MostAmazingTop10  -  6520000
                        'UCycM0Fd584iW3IpgCAjaSEw', # AmusementForce  -  6490000
                        'UCiP6wD_tYlYLYh3agzbByWQ', # FitnessBlender  -  6340000
                        'UCGi_crMdUZnrcsvkCa8pt-g', # Alltime10s  -  5690000
                        'UC9TJezP2M1ADmUYVl8hrQ2A', # BRICO SYMPA  -  5640000
                        'UCgJjd8J5moTQSwnCIx4WSIw', # Planet Dolan  -  5760000
                        'UCspJ-h5Mw9_zeEhJDzMpkkA', # Furious Pete  -  5170000
                        'UCmvqviNx70U0l4ZcvUAXxhA', # Top 5 Best  -  4790000
    ]

  elif args.set.lower() == 'notabene':
    base_channels = ['UCP46_MXP_WG_auH88FnfS1A']
    searches_to_add = []
    base_domain = 'notabene-'
    max_chans = 0
    required_recos = 2000
    skip_older_videos = False
  elif args.set.lower() == 'lama':
    base_channels = ['UCH0XvUpYcxn4V0iZGnZXMnQ']
    searches_to_add = []
    base_domain = 'lama-'
    max_chans = 0
    required_recos = 200
    skip_older_videos = False
  elif args.set.lower() == 'fr':
    base_channels = loadOrFail('base_channels/france_information_channels')
    searches_to_add = []
    base_domain = 'france-'
    max_chans = 2000
    nb_dates = 31
  elif args.set.lower() == 'hoax':
    base_channels = loadOrFail('base_channels/400_conspi_channels')
    searches_to_add = []
    base_domain = 'hoax-'
    max_chans = 0
    nb_dates = 31

  elif args.set.lower() == 'tango':
    start_videos = ["CKpbE9ks4Tw", "RnDzfKxqCF8", "_1VNc2VF4Do", "p5airQBgE6A", "XuqX5Hqx08I", "JaEsttvrXkY", "mrQBaBzqv8I",
     "Rj-wuLZB4Ho", "zpe7ZQSBerU", "qdf15Xqm_qU", "DbsBg-Ek57o", "MCUQ70s2iEQ", "B0-mwDPqQlQ", "NxbcCBl-4t0",
     "_kb0ebdHdIg", "lv3KeQ5L_FA", "98TZL-U_zSE", "cPbtvJibKEU", "0g1un0SyG6I", "F653dpMc4Uk", "ouFe9NbnU0k",
     "uWdu3wC7GYw", "J7dbAom042I", "bzLouDyOnnc", "wLMT-_0VRHY", "m2DOR7OW334", "vbHwI1hg9xQ", "IyG2DVJ_cno",
     "_0McDZ8atiY", "dBhJNxw33oo", "1fJfBCEPMXs", "qTcuxuMxNQs", "Vi4Ad3FyiZo", "K4R-uoldB3g", "njHQ5OGh_Fk",
     "g6-oN84x7IA", "3fmuPGbvUQU", "RFYqvqSGaXc", "HKY30XWHK_o", "bnC03nKl_rE", "bXeWasovHN0", "QN1Zh3mcOBM",
     "US21f5XyqVo", "D6w2IFa9fzU", "w9GKClgxYJI", "P6lFfA56BS0", "y7_ZLN9NLOk", "KiT4OS6MKVQ", "iaseGjokKi0",
     "YNjudgyBTCk", "sZh-3OvddmQ", "j2WhZazieeg", "Q6c9jLKHgzA", "RxoYyMWL788", "SemTa1iRRuI", "VdtqoOVo5Kk",
     "CgzD36KBh9A", "H7KC3n1vZaw", "SnWO_8f-1O0", "Y6fuQrcmPOs", "_j1V4jM2F6c", "vydAlB4zBz4", "OWmsR3dUlRo",
     "yVtJbnwwKjI", "vX7VjmQBD2E", "PvQ3wgo9jLc", "PnKSvq4AIz8", "7d6HEjIUow8", "JeDFuDXJm9c", "oWSugQxwpEA",
     "Hx0zzYs3td4", "AfeejNrQ2WE", "Bqtm_mpxAok", "PC6oa_Zhp0Q", "t4JXJqsGEco", "1VRj8m84Qls", "YB9yuW6jZP4",
     "Ud8VK_Dv-EA", "AvPg6x6ox4o", "uonC4FLaKmA", "_clbLO__D-8", "CjXEKqAgMJU", "Ns2urWP8qxE", "vrzpkY968ws",
     "r0qbgtBf5OE", "Oj9Wcmcyy6A", "C8-D6NfQZdU", "jRSHZzsEA8c", "pEfCDWLwp-4", "b9r6CJ-OOxo", "DC2iMlfmjiM",
     "A0oQxea2Y2U", "5XEaDLQw5jo", "SGP9f_EKcBA", "b-p0vvzLYOI", "exGtOqpeJAQ", "eJzNzTln8Tg", "sz5kDQ6kJdo",
     "J_JB_3F_jY8", "LnP6hn1MiUc", "sxm3Xyutc1s", "cr_3pNlzkjY", "X1PhkOZRmDs", "fxJ_OzuUh4k", "EWzj2nW3cJM",
     "xqS3dQeOsD0", "1buNQIu3SOc", "eQf5tPaUfcY", "n9l5btToinE", "BxhgspokY9o", "#NAME?", "25i1vPkyvJs", "oSyZZ0wRXwk",
     "ZWBVM9CwsTU", "KP_UbC9kD9Y", "xOmCA4p_fAs", "txdKKRChkMw", "_4G03HpzArc", "sE5nlW5L5GE", "s6iptZdCcG0",
     "NIf3x7izH38", "rqac_QSiGks", "HYfm69GFXDo", "TByzpZh6PSw", "c3N5m9MWPuM", "vVhN_AIZQhc", "fefQnEdfU9A",
     "vLuK-ddvu34", "L5P39TBlnQU", "paW5vC4D6-o", "caQJrO9vSPk", "M6HOiX4uicA", "p1yEPLq7f98", "ycOf18fXYcM",
     "s1L0lNiBnNM", "muUrwyVzRuM", "fjdqbJRnWG0", "IuP6InqJz2s", "UIWf-BvQ7aY", "3bn_TZ7qSC8", "BPyBKRTOQRg",
     "kjJtGuc4V4k", "fQPc-wcnjZw", "U6gKI2IYXps", "vP5RfCPVZj4", "LyMuMuGTwIk", "3v--CtoKKtk", "FM0Yitvbst0",
     "uClk6q_8uGw", "m7GinfdmeCo", "bhK9R92QCUU", "otEyrTXn8fY", "hbaI9lRlTJU", "bE1fGJnv-ds", "3F6WeJFgQW4",
     "mfqMJVe4UNE", "GrEgo9YkwKw", "l77DuRjJXnM", "xGT40wwKav8", "xHgF_wv47qk", "whnmaF8O_pI", "9CuXRw6PB1Y",
     "HOM_mwIWbZ4", "PrG3mDbFDVo", "kBEO10iecjg", "EyZq6sOLI0g", "YpLqCth7DrY", "eKF5hUYc00Y", "XuEklZZnP1s",
     "xW8prLq6awc", "nlvv0eUL_L8", "XLNJzYN2ciI", "jm4T6sYhOpQ", "2rUeoJYgLDY", "ckzMoE6U-Mw", "yI8WOJ4k-HI",
     "uins-IGlsh4", "f23vnd9VGy4", "_-dxx6qz6sI", "fgdozaYy3hY", "Ydz9jO9GAjg", "ESHA9hoKZU4", "08DQld3qt0Y",
     "NjQKE418-hg", "eUk0mK2cssI", "1jylTFt1bkE", "Gb0X6wcCM9I", "235JSb0X7Ss", "a9Gp4MFv8f4", "tO1h7XQKrNY",
     "Hkn9LamQWgc", "JJscaAVKnvA", "f2vgOVIX_us", "22v8hyBSDaY", "wf59T2VKHww", "4wjYAzMiC5U", "5d_tdSZyN6w",
     "FTyZCma9CDs", "oUzPrhl-j-E", "ziL3zCPTwj4", "gri1LAF9TLU", "JDyQLkF79PA", "GRjWNwNEs_s", "2dv7t_8gz74",
     "zlySAXbGeNY", "xYtd9bbiP9Q", "UbcpGOYtGhY", "LjtXV6JJd8s", "mjPqXh44Yww", "DD7wRFUVy44", "gSHAPjcmp0U",
     "X2Wb9cpx1uo", "r0OdM4GBMe4", "Jka-SVuQriw", "DyGLnuqQqHE", "GN-uRklmyts", "SLRZzvzyeAs", "gbm1ejw4lCI",
     "Pav_Arm2qLA", "dSK1RwjzFCc", "j1ygvGJ_wH0", "jR46o0ND7Z8"]

    # Create the YouTube scrapper
    youtube_client = YouTubeApiClient()
    youtube_scrapper = YoutubeChannelScrapper(
      youtube_client=youtube_client,
      folder='tango',
      skip_older_videos=False)

    with open('base_channels/tango_ids_vc.json', "r") as json_file:
      ids = json.load(json_file)
    all_ids = set(ids)
    top_ids = set(ids[0:400])
    print(all_ids)

    explore_set = top_ids.union(set(start_videos))

    reco_stats = collections.defaultdict(int)
    shown_ids = set()
    for video in explore_set:
      recos = youtube_scrapper.get_recommendations(video)
      for r in recos:
        reco_stats[r] += 1
        if reco_stats[r] > 1 and r not in all_ids and r not in shown_ids:
          printHTML(r)
          shown_ids.add(r)

    for r in sorted(reco_stats, key=reco_stats.get, reverse=True):
      if r not in all_ids:
        print(r + ' ' + repr(reco_stats[r]))

    return
  else:
    print('Parameter "--set" was not recognized.')
    return

  # If onlycomputerecent is true, only compute recent files and return
  if args.onlycomputerecent == 'true' or args.onlycomputerecent == 'True' or args.onlycomputerecent == '1':
    compute_recent_files(base_domain, base_channels)
    return

  # Creates the YouTube client
  youtube_client = YouTubeApiClient()

  # Check the date argument, if provided
  if args.date and args.date != '':
    if len(args.date) != 10:
      print('ERROR: Date format is wrong, should be dd-mm-yyyy')
      return
    else:
      date = args.date
  else:
    date = datetime.date.today().strftime('%d-%m-%Y')

  folder = base_domain + date
  print('Folder used: ' + folder)

  # Create the YouTube scrapper
  youtube_scrapper = YoutubeChannelScrapper(
    youtube_client=youtube_client,
    folder=folder,
    skip_older_videos=skip_older_videos)

  if args.channelstats == 'true' or args.channelstats == 'True' or args.channelstats == '1':
    youtube_scrapper.write_result_file()
    return

  if args.noscrap == 'true' or args.noscrap == 'True' or args.noscrap == '1':
    youtube_scrapper.write_result_file()
    print('No scrapping, just writing the result file for date ' + date)
    exit

  # Remove stalled channels
  try:
    stalled_channels = loadOrFail('base_channels/stalled_channels')
  except:
    print('WARNING: no stalled_channels.json file found. Assuming there are no stalled channels.')

  print('We start from ' + repr(len(base_channels)) + ' channels.')
  base_channels = [c for c in base_channels if c not in stalled_channels]
  print('There are ' + repr(len(base_channels)) + ' channels after removing stalled channels. They are:')
  base_channels = [c for c in base_channels if c not in exclude_channels]
  print('We start from ' + repr(len(base_channels)) + ' channels after removing exclueded channels. They are:')

  # Adding channels from search terms
  youtube_scrapper.add_channels_from_searches(searches_to_add, base_channels)
  print('After adding channels from searches, we now have ' + repr(len(base_channels)) + ' channels')

  for c in base_channels:
    print(c)

  # Launching the snowballing from the base channels.
  youtube_scrapper.scrap_from_base(base_channels=base_channels, max_channels=max_chans, required_recos=required_recos, only_scrap_chans_featuring_base=only_scrap_chans_featuring_base)

  print('*****************')
  print('*****************')
  print('Done with ' + folder)
  print('*****************')
  print('*****************')

  youtube_scrapper._logger.display()
  os.system('./go_push_data.bash')

  # Computing extra files with video history 
  # print('Computing recent files....')
  # compute_recent_files(base_domain, base_channels, nb_dates)
  # print('Recent files written')


if __name__ == "__main__":
    sys.exit(main())
