# YouTube Snowballs

From a given set of channel, snowballs to add channels recommended from these, and create several files to analyse the data.


### Example use:

python3.5 youtube-snowball.py --set=us
python3.5 youtube-snowball.py --set=fr

### Results

The results are stored in the folder channel-stats


### Required

A json with a YouTube API authentifications, called client_secret.json. This json can be created here:
https://console.developers.google.com/apis/credentials/consent


### Starting sets

The starting sets are stored in: 
base_channels/france_information_channels.json
base_channels/us_information_channels.json


### Optional

A list of channels not to be scrapped, in a file 'blacklisted_youtube_channels.json'


