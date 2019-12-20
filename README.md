# YouTube Snowball

From a given set of channel, snowballs to add channels recommended from these.
For each channel, the program will look at the 20 recommendations from the last video from this channel.
It will do that for all channels provided. Thereafter, it will look at which channel was recommended the most time, and add it to the initial list of channels, until it reaches the speficied number of channels.

### Install dependences

pip install --upgrade xlsxwriter
pip install --upgrade bs4
pip install --upgrade lxml
pip install --upgrade google-api-python-client
pip install --upgrade google-auth-oauthlib google-auth-httplib2

### Create a YouTube API secret 

A json with a YouTube API authentifications, called client_secret.json. This json can be created here:
https://console.developers.google.com/apis/credentials/consent

It must be kept in the root folder. 

### Example use:

python3.5 youtube-snowball.py --set=us
python3.5 youtube-snowball.py --set=fr

### Required argument:

  --set The starting set of channels. This is defined in the main function.

For instance two sets are pre-defined: 'us' or 'fr'. They were both set up to contain information channels.

New sets can be defined in the main function of the program.

### Creating new sets of initial channels:

New sets of base channels should be added in the base_channels directory. 
The parameters for each set should be defined in the main function, in particular the number of channels to be scrapped for each set.

### Results

The results are stored in the folder channel-stats

A folder will be created for each date.

There are three files in each folders: one with channel-level metadata (all_channels.json), one with video-level meta-data (api_videos.json) and one with results from scrappings (scrapped-videos.json). The last one contains the recommendations.

### Starting sets

The starting sets contain the channels from which to start from. They are stored in the folder base_channels. For instance:
 
base_channels/france_information_channels.json

This set has been handpicked manually.

### Optional

One can provide a list of channels not to be scrapped, in a file 'blacklisted_youtube_channels.json', that is stored at the root.

