import json
import socket
import urllib.parse

import requests
from dns.resolver import NXDOMAIN, NoAnswer, NoNameservers, Resolver, Timeout
from fake_useragent import UserAgent
from flask import Flask, Response, request
from requests.exceptions import SSLError

app = Flask(__name__)

def resolve_dns(hostname):
    # List of DNS servers to try
    dns_servers = [
        ['1.1.1.1', '1.0.0.1'],  # Cloudflare
        ['8.8.8.8', '8.8.4.4'],  # Google
        ['9.9.9.9', '149.112.112.112'],  # Quad9
    ]

    for servers in dns_servers:
        try:
            resolver = Resolver()
            resolver.nameservers = servers
            resolver.timeout = 2
            resolver.lifetime = 4
            answers = resolver.resolve(hostname, 'A')
            return str(answers[0])  # Return the first IP address
        except (NXDOMAIN, NoAnswer, NoNameservers, Timeout):
            continue
    return None

def curl_request(url, binary=False):
    """
    Make a request with DNS fallback and custom headers
    binary: If True, return raw bytes instead of text (for images)
    """
    try:
        ua = UserAgent()
        headers = {
            'User-Agent': ua.chrome,
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
            'Accept-Language': 'en-US,en;q=0.5',
            'Connection': 'keep-alive',
        }

        # Parse the URL to get the hostname
        parsed_url = urllib.parse.urlparse(url)
        hostname = parsed_url.hostname

        # Try to resolve DNS first
        if hostname:
            ip = resolve_dns(hostname)
            if ip:
                # Reconstruct the URL with IP address
                url_parts = list(parsed_url)
                url_parts[1] = ip  # Replace hostname with IP
                ip_url = urllib.parse.urlunparse(url_parts)

                # Try with original URL first
                try:
                    response = requests.get(url, headers=headers)
                    response.raise_for_status()
                    return response.content if binary else response.text
                except requests.RequestException:
                    # If original URL fails, try with IP
                    headers['Host'] = hostname  # Keep original hostname in Host header
                    response = requests.get(ip_url, headers=headers)
                    response.raise_for_status()
                    return response.content if binary else response.text

        # If DNS resolution fails or no hostname, try original URL
        response = requests.get(url, headers=headers)
        response.raise_for_status()
        return response.content if binary else response.text

    except SSLError:
        return {'error': 'SSL Error', 'details': 'Failed to verify SSL certificate'}, 503
    except requests.RequestException as e:
        print(f"RequestException: {e}")
        return {'error': 'Request Exception', 'details': str(e)}, 503

def encode_image_url(url):
    """Encode the image URL to be used in the proxy endpoint"""
    if not url:
        return ''
    return urllib.parse.quote(url, safe='')

@app.route('/image-proxy/<path:image_url>')
def proxy_image(image_url):
    """Proxy endpoint for images to avoid CORS issues"""
    try:
        # Decode the URL
        original_url = urllib.parse.unquote(image_url)

        # Fetch the image using our existing curl_request function with binary=True
        response = curl_request(original_url, binary=True)

        if isinstance(response, tuple):  # Error response
            return Response('', mimetype='image/png')

        # Return the image with appropriate headers
        return Response(
            response,
            mimetype='image/*',
            headers={
                'Cache-Control': 'public, max-age=31536000',
                'Access-Control-Allow-Origin': '*'
            }
        )
    except Exception as e:
        print(f"Image proxy error: {str(e)}")
        return Response('', mimetype='image/png')

@app.route('/xmltv', methods=['GET'])
def generate_xmltv():
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

    # Verify credentials first
    mainurl_response = curl_request(f'{url}/player_api.php?username={username}&password={password}')
    if isinstance(mainurl_response, tuple):
        return json.dumps(mainurl_response[0]), mainurl_response[1], {'Content-Type': 'application/json'}

    # If credentials are valid, fetch the XMLTV data directly
    base_url = url.rstrip('/')  # Remove trailing slash if present
    xmltv_response = curl_request(f'{base_url}/xmltv.php?username={username}&password={password}')

    # Get the current host URL for the proxy
    host_url = request.host_url.rstrip('/')

    if isinstance(xmltv_response, tuple):  # Check if it's an error response
        return json.dumps(xmltv_response[0]), xmltv_response[1], {'Content-Type': 'application/json'}

    # Replace image URLs in the XMLTV content
    if not isinstance(xmltv_response, tuple):
        import re

        def replace_icon_url(match):
            original_url = match.group(1)
            proxied_url = f"{host_url}/image-proxy/{encode_image_url(original_url)}"
            return f'<icon src="{proxied_url}"'

        # Replace icon URLs in the XML
        xmltv_response = re.sub(
            r'<icon src="([^"]+)"',
            replace_icon_url,
            xmltv_response
        )

    # If unwanted_groups is specified, we need to filter the XML
    if unwanted_groups:
        try:
            # Fetch categories and channels to get the mapping
            category_response = curl_request(f'{url}/player_api.php?username={username}&password={password}&action=get_live_categories')
            livechannel_response = curl_request(f'{url}/player_api.php?username={username}&password={password}&action=get_live_streams')

            if not isinstance(category_response, tuple) and not isinstance(livechannel_response, tuple):
                categories = json.loads(category_response)
                channels = json.loads(livechannel_response)

                # Create category mapping
                category_names = {cat['category_id']: cat['category_name'] for cat in categories}

                # Create set of channel IDs to exclude
                excluded_channels = {
                    str(channel['stream_id'])
                    for channel in channels
                    if channel['stream_type'] == 'live'
                    and any(
                        unwanted_group.lower() in category_names.get(channel['category_id'], '').lower()
                        for unwanted_group in unwanted_groups
                    )
                }

                if excluded_channels:
                    # Simple XML filtering using string operations
                    filtered_lines = []
                    current_channel = None
                    skip_current = False

                    for line in xmltv_response.split('\n'):
                        if '<channel id="' in line:
                            current_channel = line.split('"')[1]
                            skip_current = current_channel in excluded_channels

                        if not skip_current:
                            if '<programme ' in line:
                                channel_id = line.split('channel="')[1].split('"')[0]
                                skip_current = channel_id in excluded_channels

                            if not skip_current:
                                filtered_lines.append(line)

                        if '</channel>' in line or '</programme>' in line:
                            skip_current = False

                    xmltv_response = '\n'.join(filtered_lines)

        except (json.JSONDecodeError, IndexError, KeyError):
            # If filtering fails, return unfiltered XMLTV
            pass

    # Return the modified XMLTV data
    return Response(
        xmltv_response,
        mimetype='application/xml',
        headers={"Content-Disposition": "attachment; filename=guide.xml"}
    )

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

    # Get the current host URL for the proxy
    host_url = request.host_url.rstrip('/')

    # Generate M3U playlist
    m3u_playlist = "#EXTM3U\n"
    for channel in livechannelraw:
        if channel['stream_type'] == 'live':
            group_title = categoryname.get(channel["category_id"], "Uncategorized")
            if not any(unwanted_group.lower() in group_title.lower() for unwanted_group in unwanted_groups):
                # Proxy the logo URL
                original_logo = channel.get('stream_icon', '')
                logo_url = f"{host_url}/image-proxy/{encode_image_url(original_logo)}" if original_logo else ''

                m3u_playlist += f'#EXTINF:0 tvg-name="{channel["name"]}" group-title="{group_title}" tvg-logo="{logo_url}",{channel["name"]}\n'
                m3u_playlist += f'{fullurl}{channel["stream_id"]}.ts\n'

    # Return the M3U playlist as a downloadable file
    return Response(m3u_playlist, mimetype='audio/x-scpls', headers={"Content-Disposition": "attachment; filename=LiveStream.m3u"})

if __name__ == '__main__':
    app.run(debug=True)