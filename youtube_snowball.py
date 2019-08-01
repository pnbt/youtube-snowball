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

from bs4 import BeautifulSoup
from urllib.request import urlopen

# Google Imports
import google.oauth2.credentials
import google_auth_oauthlib.flow

from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from google_auth_oauthlib.flow import InstalledAppFlow
from google.oauth2 import service_account


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

# The CLIENT_SECRETS_FILE variable specifies the name of a file that contains
# the OAuth 2.0 information for this application, including its client_id and
# client_secret.
CLIENT_SECRETS_FILE = "client_secret_other.json"
API_SERVICE_NAME = 'youtube'
API_VERSION = 'v3'

class YouTubeApiClient():
  """ This class is a client that interfaces with the YouTube API."""

  def __init__(self):
    os.environ['OAUTHLIB_INSECURE_TRANSPORT'] = '1'
    self._client = self.get_authenticated_service()

  def get_authenticated_service(self):
    """ Create an authentificated client for YouTube API. """
    credentials = service_account.Credentials.from_service_account_file("client_secret.json")

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
      try:
        return the_function(**kwargs)
      except Exception as e:
        # In case of exception, we print it and sleep 30 seconds.
        print(e)
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

class YoutubeChannelScrapper():
  """ Class that scraps YouTube channels. """

  def __init__(self, youtube_client, folder):
    try:
      os.mkdir(DATA_DIRECTORY + folder)
    except:
      pass
    self._youtube_client = youtube_client
    self._folder = folder

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
    for video in sorted_videos[0:100]:
      try:
        print(repr(self._api_videos[video].get('statistics', {})['viewCount']) + ' - ' + self._api_videos[video]['snippet']['title'])
      except:
        print('WARNING, A VIDEO IN THE TOP 100 HAS NO VIEWCOUNT')
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
    self.getVideosFromYouTubeAPI(reco)
    return self._api_videos[reco]['snippet']['channelId']

  def get_recommendations(self, video_id):
    """ Returns the recommendations for a given video. If it was not scrapped before,
        the video will be scrapped, and its information added to self._scrapped_videos

        :param video_id: the id of the video
        :returns: recommendations from that video
    """

    if video_id in self._scrapped_videos:
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
            time.sleep(1)
    soup = BeautifulSoup(html, "lxml")

    # Getting views
    views = -1
    for watch_count in soup.findAll('div', {'class': 'watch-view-count'}):
        try:
            views = self.clean_count(watch_count.contents[0])
        except IndexError:
            pass

    # Getting likes
    likes = -1
    for like_count in soup.findAll('button', {'class': 'like-button-renderer-like-button'}):
        try:
            likes = self.clean_count(like_count.contents[0].text)
        except IndexError:
            pass

    # Getting dislikes
    dislikes = -1
    for like_count in soup.findAll('button', {'class': 'like-button-renderer-dislike-button'}):
        try:
            dislikes = self.clean_count(like_count.contents[0].text)
        except IndexError:
            pass

    # Getting duration
    duration = -1
    for time_count in soup.findAll('meta', {'itemprop': 'duration'}):
        try:
            dur = time_count['content'].replace('PT', '')
            duration = 0
            if 'H' in dur:
                contents = dur.split('H')
                duration += int(contents[0]) * 3600
                dur = contents[1]
            if 'M' in dur:
                contents = dur.split('M')
                duration += int(contents[0]) * 60
                dur = contents[1]
            if 'S' in dur:
                contents = dur.split('S')
                duration += int(contents[0])
        except IndexError:
            pass

    # Getting publication date
    pubdate = ""
    for datefield in soup.findAll('meta', {'itemprop': 'datePublished'}):
        try:
            pubdate = datefield['content']
        except IndexError:
            pass

    # Getting Channel
    channel = ''
    channel_id = ''
    for item_section in soup.findAll('a', {'class': 'yt-uix-sessionlink'}):
        if item_section['href'] and '/channel/' in item_section['href'] and item_section.contents[0] != '\n':
            channel = item_section.contents[0]
            channel_id = item_section['href'].split('/channel/')[1]
            break

    if channel == '':
        print('WARNING: We could not find the channel of the video ' + video_id)

    # Getting recommendations
    recos = []
    # Up next
    for video_list in soup.findAll('li', {'class':"video-list-item related-list-item show-video-time"}):
        try:
            recos.append(video_list.contents[1].contents[1]['href'].replace('/watch?v=', ''))
        except IndexError:
            print ('WARNING Could not get a UP NEXT RECOMMENDATION')
            pass
    # Others
    for video_list in soup.findAll('li', {'class':"video-list-item related-list-item show-video-time related-list-item-compact-video"}):    
        try:
            recos.append(video_list.contents[1].contents[1]['href'].replace('/watch?v=', ''))
        except IndexError:
            print ('WARNING Could not get a RECOMMENDATION')
            pass

    # Getting title
    title = ''
    for eow_title in soup.findAll('span', {'id': 'eow-title'}):
        title = eow_title.text.strip()

    if title == '':
        print ('WARNING: title not found')

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
                                        'channel_id': channel_id}

    video = self._scrapped_videos[video_id]
    print(video_id + ': ' + video['title'] + ' [' + channel + ']' + str(video['views']) + ' views and ' + repr(len(video['recommendations'])) + ' recommendations')
    return recos

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
      if len(channelResponse['items']) == 0:
        print('ERROR SCRAPPING CHANNEL ' + channel)
        return

      listResponse = self._youtube_client.playlists_list_by_id(
          part='snippet,contentDetails',
          playlistId=channelResponse['items'][0]['contentDetails']['relatedPlaylists']['uploads'],
          maxResults=LATEST_VIDEOS)
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


  def write_result_file(self):
    """ Write file with videos that were recommended the most. """

    # 1 compute number of recos per video
    videos_to_recos = collections.defaultdict(int)
    for video in self._scrapped_videos:
      for reco in self._scrapped_videos[video]['recommendations']:
        videos_to_recos[reco] += 1

    nb_ok_vids = 0
    nb_not_ok_vids = 0
    final_dict = {'info_channels':[]}
    for video in sorted(videos_to_recos, key=videos_to_recos.get, reverse=True):

      # We only save videos that have been viewed more than once in order to save space.
      if videos_to_recos[video] > 1:
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
            "comments": int(video_info.get('statistics', {}).get('commentCount', 0))
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
            "channel": video_info['channel']
          })
        else:
          nb_not_ok_vids += 1
          print('WARNING: one video not in API VIDEOS will be ignored despite beeing recommended ' + str(videos_to_recos[video]) + ' out of ' + str(nb_ok_vids))

    print(' Videos with info ok '+ repr(nb_ok_vids) + ' not ok '+ repr(nb_not_ok_vids))
    self.saveToFile(final_dict, DATA_DIRECTORY+ self._folder)
    print('Result file written! ')

  def describe_channels(self):
    """ Print the 500 top channels by recommendations. """

    # Computing number of recommendations
    total_channel_stats = collections.defaultdict(int)
    for unused_vid, info in self._scrapped_videos.items():
      for reco in info['recommendations']:
        total_channel_stats[self._video_to_chan_map.get(reco, 'unknown')] += 1

    for chan in sorted(total_channel_stats, key=total_channel_stats.get, reverse=True)[0:500]:
      try:
        print('\n\n\n' + str(total_channel_stats[chan]))
        print(self._channel_stats[chan]['snippet']['title'] + ' '  + chan)
        print(self._channel_stats[chan]['snippet']['description'] + ' ')
      except:
        print(' ' + str(total_channel_stats[chan]) + ' ' + chan)

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
  video_to_chan = loadFromFile(VIDEO_TO_CHAN_FILE)

  api_videos = {}
  api_videos_date = collections.defaultdict(dict)

  for date in dates:
      folder = makeFolder(date)
      api_videos_date[date] = loadFromFile(DATA_DIRECTORY + folder + '/api_videos.json')
      api_videos.update(api_videos_date[date])
      for vid in api_videos_date[date]:
          v_to_channame[vid] = api_videos_date[date][vid]['snippet']['channelTitle']

  all_videos = set(all_scrapped_vids).union(set(api_videos.keys()))

  # Delete scrapped videos that returned empty data.
  nb_deleted = 0
  for date in dates:
      for v in list(scrapped_videos[date].keys()):
          if scrapped_videos[date][v]['title'] == '':
              del scrapped_videos[date][v]

  # Computing the number of videos to recommendations.
  video_to_recos = collections.defaultdict(int)
  video_to_recos_date = {}
  video_to_top_recs = collections.defaultdict(int)
  rdates = list(reversed(dates))
  inc = 0
  dec = 0

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
    original_channels_names.add(channel_stats.get(c, {}).get('snippet', {}).get('channelTitle', 'Unknown channel name'))

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

      if view_increase and view_increase > 1000 and video_data['observed_recos']/view_increase > 100:
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

def main():
  global parser
  # Reading command line arguments
  parser = argparse.ArgumentParser(description=__doc__)
  parser.add_argument('--set', help='The starting set of channels')
  parser.add_argument('--date', help='The date from which the scrapping is done, in format dd-mm-yyyy')
  parser.add_argument('--noscrap', help='Skip scrapping')
  parser.add_argument('--onlycomputerecent', help='If we only compute recent files')
  args = parser.parse_args()

  # Setting different parameters for different datasets
  only_scrap_chans_featuring_base = False
  required_recos = REQUIRED_RECOS
  if args.set.lower() == 'us':
    base_channels = loadOrFail('base_channels/us_information_channels')
    base_domain = 'us-info-'
    searches_to_add = []
    max_chans = 2000
    nb_dates = 31
  elif args.set.lower() == 'fr':
    base_channels = loadOrFail('base_channels/france_information_channels')
    searches_to_add = []
    base_domain = 'france-'
    max_chans = 2000
    nb_dates = 31
  else:
    print('Parameter "--set" was not recognized. It should be fr or us')
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
  youtube_scrapper = YoutubeChannelScrapper(youtube_client=youtube_client, folder=folder)

  if args.noscrap == 'true' or args.noscrap == 'True' or args.noscrap == '1':
    youtube_scrapper.write_result_file()
    print('No scrapping, just writing the result file for date ' + date)
    exit

  print('We start from ' + repr(len(base_channels)) + ' channels')

  # Adding channels from search terms
  youtube_scrapper.add_channels_from_searches(searches_to_add, base_channels)
  print('After adding channels from searches, we now have ' + repr(len(base_channels)) + ' channels')
  youtube_scrapper.describe_channels()

  # Launching the snowballing from the base channels.
  youtube_scrapper.scrap_from_base(base_channels=base_channels, max_channels=max_chans, required_recos=required_recos, only_scrap_chans_featuring_base=only_scrap_chans_featuring_base)

  print('*****************')
  print('*****************')
  print('Done with ' + folder)
  print('*****************')
  print('*****************')

  # Computing extra files with video history 
  print('Computing recent files....')
  compute_recent_files(base_domain, base_channels, nb_dates)
  print('Recent files written')

if __name__ == "__main__":
    sys.exit(main())
