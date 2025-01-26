import json
import logging
import urllib.parse

import requests
from fake_useragent import UserAgent
from flask import Flask, Response, request
from requests.exceptions import SSLError

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__)

def curl_request(url, binary=False):
    """
    Make a request with custom headers
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
        logger.info(f"Image proxy request for: {original_url}")

        # Make request with stream=True and timeout
        response = requests.get(original_url, stream=True, timeout=10)
        response.raise_for_status()

        # Get content type from response
        content_type = response.headers.get('Content-Type', '')
        logger.info(f"Image response headers: {dict(response.headers)}")

        if not content_type.startswith('image/'):
            logger.error(f"Invalid content type for image: {content_type}")
            return Response('Invalid image type', status=415)

        def generate():
            try:
                bytes_sent = 0
                for chunk in response.iter_content(chunk_size=8192):
                    if chunk:
                        bytes_sent += len(chunk)
                        yield chunk
                logger.info(f"Image completed, sent {bytes_sent} bytes")
            except Exception as e:
                logger.error(f"Image streaming error in generator: {str(e)}")
                raise

        headers = {
            'Cache-Control': 'public, max-age=31536000',
            'Access-Control-Allow-Origin': '*',
        }

        # Only add Content-Length if we have it and it's not chunked transfer
        if ('Content-Length' in response.headers and
            'Transfer-Encoding' not in response.headers):
            headers['Content-Length'] = response.headers['Content-Length']
        else:
            headers['Transfer-Encoding'] = 'chunked'

        logger.info(f"Sending image response with headers: {headers}")

        return Response(
            generate(),
            mimetype=content_type,
            headers=headers
        )
    except requests.Timeout:
        logger.error(f"Timeout fetching image: {original_url}")
        return Response('Image fetch timeout', status=504)
    except requests.HTTPError as e:
        logger.error(f"HTTP error fetching image: {str(e)}")
        return Response(f'Failed to fetch image: {str(e)}', status=e.response.status_code)
    except Exception as e:
        logger.error(f"Image proxy error: {str(e)}")
        return Response('Failed to process image', status=500)

@app.route('/stream-proxy/<path:stream_url>')
def proxy_stream(stream_url):
    """Proxy endpoint for streams"""
    try:
        # Decode the URL
        original_url = urllib.parse.unquote(stream_url)
        logger.info(f"Stream proxy request for: {original_url}")

        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
        }

        # Add timeout to prevent hanging
        response = requests.get(original_url, stream=True, headers=headers, timeout=10)
        response.raise_for_status()

        logger.info(f"Stream response headers: {dict(response.headers)}")

        # Get content type from response
        content_type = response.headers.get('Content-Type')
        if not content_type:
            # Try to determine content type from URL
            if original_url.endswith('.ts'):
                content_type = 'video/MP2T'
            elif original_url.endswith('.m3u8'):
                content_type = 'application/vnd.apple.mpegurl'
            else:
                content_type = 'application/octet-stream'

        logger.info(f"Using content type: {content_type}")

        def generate():
            try:
                bytes_sent = 0
                for chunk in response.iter_content(chunk_size=64*1024):
                    if chunk:
                        bytes_sent += len(chunk)
                        yield chunk
                logger.info(f"Stream completed, sent {bytes_sent} bytes")
            except Exception as e:
                logger.error(f"Streaming error in generator: {str(e)}")
                raise

        response_headers = {
            'Access-Control-Allow-Origin': '*',
            'Content-Type': content_type,
            'Accept-Ranges': 'bytes',
            'Cache-Control': 'no-cache',
            'Connection': 'keep-alive'
        }

        # Only add Content-Length if we have it and it's not chunked transfer
        if ('Content-Length' in response.headers and
            'Transfer-Encoding' not in response.headers):
            response_headers['Content-Length'] = response.headers['Content-Length']
        else:
            response_headers['Transfer-Encoding'] = 'chunked'

        logger.info(f"Sending response with headers: {response_headers}")

        return Response(
            generate(),
            headers=response_headers,
            direct_passthrough=True
        )
    except requests.Timeout:
        logger.error(f"Timeout fetching stream: {original_url}")
        return Response('Stream timeout', status=504)
    except requests.HTTPError as e:
        logger.error(f"HTTP error fetching stream: {str(e)}")
        return Response(f'Failed to fetch stream: {str(e)}', status=e.response.status_code)
    except Exception as e:
        logger.error(f"Stream proxy error: {str(e)}")
        return Response('Failed to process stream', status=500)

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
    no_stream_proxy = request.args.get('nostreamproxy', '').lower() == 'true'

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

                stream_url = f'{fullurl}{channel["stream_id"]}.ts'
                if not no_stream_proxy:
                    stream_url = f"{host_url}/stream-proxy/{encode_image_url(stream_url)}"

                m3u_playlist += f'#EXTINF:0 tvg-name="{channel["name"]}" group-title="{group_title}" tvg-logo="{logo_url}",{channel["name"]}\n'
                m3u_playlist += f'{stream_url}\n'

    # Return the M3U playlist as a downloadable file
    return Response(m3u_playlist, mimetype='audio/x-scpls', headers={"Content-Disposition": "attachment; filename=LiveStream.m3u"})

if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0')