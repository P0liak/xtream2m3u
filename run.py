import json

import requests
from fake_useragent import UserAgent
from flask import Flask, Response, request
from requests.exceptions import SSLError

app = Flask(__name__)

def curl_request(url):
    try:
        ua = UserAgent()
        headers = {
            'User-Agent': ua.chrome,
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
            'Accept-Language': 'en-US,en;q=0.5',
            'Connection': 'keep-alive',
        }
        response = requests.get(url, headers=headers)
        response.raise_for_status()
        return response.text
    except SSLError:
        return {'error': 'SSL Error', 'details': 'Failed to verify SSL certificate'}, 503
    except requests.RequestException as e:
        print(f"RequestException: {e}")
        return {'error': 'Request Exception', 'details': str(e)}, 503

@app.route('/m3u', methods=['GET'])
def generate_m3u():
    # Get parameters from the URL
    url = request.args.get('url')
    username = request.args.get('username')
    password = request.args.get('password')
    unwanted_groups = request.args.get('unwanted_groups', '')

    if not url or not username or not password:
        return json.dumps({
            'error': 'Missing Parameters',
            'details': 'Required parameters: url, username, and password'
        }), 400, {'Content-Type': 'application/json'}

    # Convert unwanted groups into a list
    unwanted_groups = [group.strip() for group in unwanted_groups.split(',')] if unwanted_groups else []

    # Verify the credentials and the provided URL
    mainurl_response = curl_request(f'{url}/player_api.php?username={username}&password={password}')
    if isinstance(mainurl_response, tuple):  # Check if it's an error response
        return json.dumps(mainurl_response[0]), mainurl_response[1], {'Content-Type': 'application/json'}
    mainurl_json = mainurl_response

    try:
        mainurlraw = json.loads(mainurl_json)
    except json.JSONDecodeError as e:
        return json.dumps({
            'error': 'Invalid JSON',
            'details': f'Failed to parse server response: {str(e)}'
        }), 500, {'Content-Type': 'application/json'}

    if 'user_info' not in mainurlraw or 'server_info' not in mainurlraw:
        return json.dumps({
            'error': 'Invalid Response',
            'details': 'Server response missing required data (user_info or server_info)'
        }), 400, {'Content-Type': 'application/json'}

    # Fetch live streams
    livechannel_response = curl_request(f'{url}/player_api.php?username={username}&password={password}&action=get_live_streams')
    if isinstance(livechannel_response, tuple):  # Check if it's an error response
        return json.dumps(livechannel_response[0]), livechannel_response[1], {'Content-Type': 'application/json'}
    livechannel_json = livechannel_response

    try:
        livechannelraw = json.loads(livechannel_json)
    except json.JSONDecodeError as e:
        return json.dumps({
            'error': 'Invalid JSON',
            'details': f'Failed to parse live streams data: {str(e)}'
        }), 500, {'Content-Type': 'application/json'}

    if not isinstance(livechannelraw, list):
        return json.dumps({
            'error': 'Invalid Data Format',
            'details': 'Live streams data is not in the expected format'
        }), 500, {'Content-Type': 'application/json'}

    # Fetch live categories
    category_response = curl_request(f'{url}/player_api.php?username={username}&password={password}&action=get_live_categories')
    if isinstance(category_response, tuple):  # Check if it's an error response
        return json.dumps(category_response[0]), category_response[1], {'Content-Type': 'application/json'}
    category_json = category_response

    try:
        categoryraw = json.loads(category_json)
    except json.JSONDecodeError as e:
        return json.dumps({
            'error': 'Invalid JSON',
            'details': f'Failed to parse categories data: {str(e)}'
        }), 500, {'Content-Type': 'application/json'}

    if not isinstance(categoryraw, list):
        return json.dumps({
            'error': 'Invalid Data Format',
            'details': 'Categories data is not in the expected format'
        }), 500, {'Content-Type': 'application/json'}

    username = mainurlraw['user_info']['username']
    password = mainurlraw['user_info']['password']

    server_url = f"http://{mainurlraw['server_info']['url']}:{mainurlraw['server_info']['port']}"
    fullurl = f"{server_url}/live/{username}/{password}/"

    categoryname = {cat['category_id']: cat['category_name'] for cat in categoryraw}

    # Generate M3U playlist
    m3u_playlist = "#EXTM3U\n"
    for channel in livechannelraw:
        if channel['stream_type'] == 'live':
            # Use a default category name if category_id is None
            group_title = categoryname.get(channel["category_id"], "Uncategorized")
            # Skip this channel if its group is in the unwanted list
            if not any(unwanted_group.lower() in group_title.lower() for unwanted_group in unwanted_groups):
                logo_url = channel.get('stream_icon', '')
                m3u_playlist += f'#EXTINF:0 tvg-name="{channel["name"]}" group-title="{group_title}" tvg-logo="{logo_url}",{channel["name"]}\n'
                m3u_playlist += f'{fullurl}{channel["stream_id"]}.ts\n'

    # Return the M3U playlist as a downloadable file
    return Response(m3u_playlist, mimetype='audio/x-scpls', headers={"Content-Disposition": "attachment; filename=LiveStream.m3u"})

if __name__ == '__main__':
    app.run(debug=True)