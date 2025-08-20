import fnmatch
import ipaddress
import json
import logging
import os
import re
import socket
import urllib.parse
from functools import lru_cache

import dns.resolver
import requests
from fake_useragent import UserAgent
from flask import Flask, Response, request

# Configure logging
logging.basicConfig(level=logging.WARNING)
logger = logging.getLogger(__name__)

app = Flask(__name__)

# Get default proxy URL from environment variable
DEFAULT_PROXY_URL = os.environ.get('PROXY_URL')

# Set up custom DNS resolver
def setup_custom_dns():
    """Configure a custom DNS resolver using reliable DNS services"""
    dns_servers = ['1.1.1.1', '1.0.0.1', '8.8.8.8', '8.8.4.4', '9.9.9.9']

    custom_resolver = dns.resolver.Resolver()
    custom_resolver.nameservers = dns_servers

    original_getaddrinfo = socket.getaddrinfo

    def new_getaddrinfo(host, port, family=0, type=0, proto=0, flags=0):
        if host:
            try:
                # Skip DNS resolution for IP addresses
                try:
                    ipaddress.ip_address(host)
                    # If we get here, the host is already an IP address
                    logger.debug(f"Host is already an IP address: {host}, skipping DNS resolution")
                except ValueError:
                    # Not an IP address, so use DNS resolution
                    answers = custom_resolver.resolve(host)
                    host = str(answers[0])
                    logger.debug(f"Custom DNS resolved {host}")
            except Exception as e:
                logger.info(f"Custom DNS resolution failed for {host}: {e}, falling back to system DNS")
        return original_getaddrinfo(host, port, family, type, proto, flags)

    socket.getaddrinfo = new_getaddrinfo
    logger.info("Custom DNS resolver set up")

# Initialize DNS resolver
setup_custom_dns()

# Common request function with caching for API endpoints
@lru_cache(maxsize=128)
def fetch_api_data(url, timeout=10):
    """Make a request to an API endpoint with caching"""
    ua = UserAgent()
    headers = {
        'User-Agent': ua.chrome,
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
        'Accept-Language': 'en-US,en;q=0.5',
        'Connection': 'keep-alive',
    }

    try:
        hostname = urllib.parse.urlparse(url).netloc.split(':')[0]
        logger.info(f"Making request to host: {hostname}")

        response = requests.get(url, headers=headers, timeout=timeout)
        response.raise_for_status()

        # Try to parse as JSON
        try:
            return json.loads(response.text)
        except json.JSONDecodeError:
            # Return text if not valid JSON
            return response.text

    except requests.exceptions.SSLError:
        return {'error': 'SSL Error', 'details': 'Failed to verify SSL certificate'}, 503
    except requests.exceptions.RequestException as e:
        logger.error(f"RequestException: {e}")
        return {'error': 'Request Exception', 'details': str(e)}, 503

def stream_request(url, headers=None, timeout=10):
    """Make a streaming request that doesn't buffer the full response"""
    if not headers:
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
        }

    return requests.get(url, stream=True, headers=headers, timeout=timeout)

def encode_url(url):
    """Safely encode a URL for use in proxy endpoints"""
    return urllib.parse.quote(url, safe='') if url else ''

def generate_streaming_response(response, content_type=None):
    """Generate a streaming response with appropriate headers"""
    if not content_type:
        content_type = response.headers.get('Content-Type', 'application/octet-stream')

    def generate():
        try:
            bytes_sent = 0
            for chunk in response.iter_content(chunk_size=8192):
                if chunk:
                    bytes_sent += len(chunk)
                    yield chunk
            logger.info(f"Stream completed, sent {bytes_sent} bytes")
        except Exception as e:
            logger.error(f"Streaming error: {str(e)}")
            raise

    headers = {
        'Access-Control-Allow-Origin': '*',
        'Content-Type': content_type,
    }

    # Add content length if available and not using chunked transfer
    if 'Content-Length' in response.headers and 'Transfer-Encoding' not in response.headers:
        headers['Content-Length'] = response.headers['Content-Length']
    else:
        headers['Transfer-Encoding'] = 'chunked'

    return Response(
        generate(),
        mimetype=content_type,
        headers=headers,
        direct_passthrough=True
    )

@app.route('/image-proxy/<path:image_url>')
def proxy_image(image_url):
    """Proxy endpoint for images to avoid CORS issues"""
    try:
        original_url = urllib.parse.unquote(image_url)
        logger.info(f"Image proxy request for: {original_url}")

        response = requests.get(original_url, stream=True, timeout=10)
        response.raise_for_status()

        content_type = response.headers.get('Content-Type', '')

        if not content_type.startswith('image/'):
            logger.error(f"Invalid content type for image: {content_type}")
            return Response('Invalid image type', status=415)

        return generate_streaming_response(response, content_type)
    except requests.Timeout:
        return Response('Image fetch timeout', status=504)
    except requests.HTTPError as e:
        return Response(f'Failed to fetch image: {str(e)}', status=e.response.status_code)
    except Exception as e:
        logger.error(f"Image proxy error: {str(e)}")
        return Response('Failed to process image', status=500)

@app.route('/stream-proxy/<path:stream_url>')
def proxy_stream(stream_url):
    """Proxy endpoint for streams"""
    try:
        original_url = urllib.parse.unquote(stream_url)
        logger.info(f"Stream proxy request for: {original_url}")

        response = stream_request(original_url)
        response.raise_for_status()

        # Determine content type
        content_type = response.headers.get('Content-Type')
        if not content_type:
            if original_url.endswith('.ts'):
                content_type = 'video/MP2T'
            elif original_url.endswith('.m3u8'):
                content_type = 'application/vnd.apple.mpegurl'
            else:
                content_type = 'application/octet-stream'

        logger.info(f"Using content type: {content_type}")
        return generate_streaming_response(response, content_type)
    except requests.Timeout:
        return Response('Stream timeout', status=504)
    except requests.HTTPError as e:
        return Response(f'Failed to fetch stream: {str(e)}', status=e.response.status_code)
    except Exception as e:
        logger.error(f"Stream proxy error: {str(e)}")
        return Response('Failed to process stream', status=500)

def parse_group_list(group_string):
    """Parse a comma-separated string into a list of trimmed strings"""
    return [group.strip() for group in group_string.split(',')] if group_string else []

def group_matches(group_title, pattern):
    """Check if a group title matches a pattern, supporting wildcards and exact matching"""
    # Convert to lowercase for case-insensitive matching
    group_lower = group_title.lower()
    pattern_lower = pattern.lower()

    # Handle spaces in pattern
    if ' ' in pattern_lower:
        # For patterns with spaces, split and check each part
        pattern_parts = pattern_lower.split()
        group_parts = group_lower.split()

        # If pattern has more parts than group, can't match
        if len(pattern_parts) > len(group_parts):
            return False

        # Check each part of the pattern against group parts
        for i, part in enumerate(pattern_parts):
            if i >= len(group_parts):
                return False
            if '*' in part or '?' in part:
                if not fnmatch.fnmatch(group_parts[i], part):
                    return False
            else:
                if part not in group_parts[i]:
                    return False
        return True

    # Check for wildcard patterns
    if '*' in pattern_lower or '?' in pattern_lower:
        return fnmatch.fnmatch(group_lower, pattern_lower)
    else:
        # Simple substring match for non-wildcard patterns
        return pattern_lower in group_lower

def get_required_params():
    """Get and validate the required parameters from the request"""
    url = request.args.get('url')
    username = request.args.get('username')
    password = request.args.get('password')

    if not url or not username or not password:
        return None, None, None, json.dumps({
            'error': 'Missing Parameters',
            'details': 'Required parameters: url, username, and password'
        }), 400

    proxy_url = request.args.get('proxy_url', DEFAULT_PROXY_URL) or request.host_url.rstrip('/')

    return url, username, password, proxy_url, None

def validate_xtream_credentials(url, username, password):
    """Validate the Xtream API credentials"""
    api_url = f'{url}/player_api.php?username={username}&password={password}'
    data = fetch_api_data(api_url)

    if isinstance(data, tuple):  # Error response
        return None, data[0], data[1]

    if 'user_info' not in data or 'server_info' not in data:
        return None, json.dumps({
            'error': 'Invalid Response',
            'details': 'Server response missing required data (user_info or server_info)'
        }), 400

    return data, None, None

def fetch_categories_and_channels(url, username, password):
    """Fetch categories and channels from the Xtream API"""
    # Fetch categories
    category_url = f'{url}/player_api.php?username={username}&password={password}&action=get_live_categories'
    categories = fetch_api_data(category_url)

    if isinstance(categories, tuple):  # Error response
        return None, None, categories[0], categories[1]

    # Fetch live channels
    channel_url = f'{url}/player_api.php?username={username}&password={password}&action=get_live_streams'
    channels = fetch_api_data(channel_url)

    if isinstance(channels, tuple):  # Error response
        return None, None, channels[0], channels[1]

    if not isinstance(categories, list) or not isinstance(channels, list):
        return None, None, json.dumps({
            'error': 'Invalid Data Format',
            'details': 'Categories or channels data is not in the expected format'
        }), 500

    return categories, channels, None, None

@app.route('/xmltv', methods=['GET'])
def generate_xmltv():
    """Generate a filtered XMLTV file from the Xtream API"""
    # Get and validate parameters
    url, username, password, proxy_url, error = get_required_params()
    if error:
        return error

    # No filtering supported for XMLTV endpoint

    # Validate credentials
    user_data, error_json, error_code = validate_xtream_credentials(url, username, password)
    if error_json:
        return error_json, error_code, {'Content-Type': 'application/json'}

    # Fetch XMLTV data
    base_url = url.rstrip('/')
    xmltv_url = f'{base_url}/xmltv.php?username={username}&password={password}'
    xmltv_data = fetch_api_data(xmltv_url, timeout=20)  # Longer timeout for XMLTV

    if isinstance(xmltv_data, tuple):  # Error response
        return json.dumps(xmltv_data[0]), xmltv_data[1], {'Content-Type': 'application/json'}

    # If not proxying, return the original XMLTV
    if not proxy_url:
        return Response(
            xmltv_data,
            mimetype='application/xml',
            headers={"Content-Disposition": "attachment; filename=guide.xml"}
        )

    # Replace image URLs in the XMLTV content with proxy URLs
    def replace_icon_url(match):
        original_url = match.group(1)
        proxied_url = f"{proxy_url}/image-proxy/{encode_url(original_url)}"
        return f'<icon src="{proxied_url}"'

    xmltv_data = re.sub(
        r'<icon src="([^"]+)"',
        replace_icon_url,
        xmltv_data
    )

    # Return the XMLTV data
    return Response(
        xmltv_data,
        mimetype='application/xml',
        headers={"Content-Disposition": "attachment; filename=guide.xml"}
    )

@app.route('/m3u', methods=['GET'])
def generate_m3u():
    """Generate a filtered M3U playlist from the Xtream API"""
    # Get and validate parameters
    url, username, password, proxy_url, error = get_required_params()
    if error:
        return error

    # Parse filter parameters
    unwanted_groups = parse_group_list(request.args.get('unwanted_groups', ''))
    wanted_groups = parse_group_list(request.args.get('wanted_groups', ''))
    no_stream_proxy = request.args.get('nostreamproxy', '').lower() == 'true'

    # Log filter parameters
    logger.info(f"Filter parameters - wanted_groups: {wanted_groups}, unwanted_groups: {unwanted_groups}")

    # Validate credentials
    user_data, error_json, error_code = validate_xtream_credentials(url, username, password)
    if error_json:
        return error_json, error_code, {'Content-Type': 'application/json'}

    # Fetch categories and channels
    categories, channels, error_json, error_code = fetch_categories_and_channels(url, username, password)
    if error_json:
        return error_json, error_code, {'Content-Type': 'application/json'}

    # Extract user info and server URL
    username = user_data['user_info']['username']
    password = user_data['user_info']['password']

    server_url = f"http://{user_data['server_info']['url']}:{user_data['server_info']['port']}"
    stream_base_url = f"{server_url}/live/{username}/{password}/"

    # Create category name lookup
    category_names = {cat['category_id']: cat['category_name'] for cat in categories}

    # Log all available groups
    all_groups = set(category_names.values())
    logger.info(f"All available groups: {sorted(all_groups)}")

    # Generate M3U playlist
    m3u_playlist = "#EXTM3U\n"

    # Track included groups
    included_groups = set()

    for channel in channels:
        if channel['stream_type'] == 'live':
            group_title = category_names.get(channel["category_id"], "Uncategorized")

            # Handle filtering logic
            include_channel = True

            if wanted_groups:
                # Only include channels from specified groups
                include_channel = any(group_matches(group_title, wanted_group) for wanted_group in wanted_groups)
            elif unwanted_groups:
                # Exclude channels from unwanted groups
                include_channel = not any(group_matches(group_title, unwanted_group) for unwanted_group in unwanted_groups)

            if include_channel:
                included_groups.add(group_title)
                # Proxy the logo URL if available
                original_logo = channel.get('stream_icon', '')
                logo_url = f"{proxy_url}/image-proxy/{encode_url(original_logo)}" if original_logo else ''

                # Create the stream URL with or without proxying
                stream_url = f'{stream_base_url}{channel["stream_id"]}.ts'
                if not no_stream_proxy:
                    stream_url = f"{proxy_url}/stream-proxy/{encode_url(stream_url)}"

                # Add channel to playlist
                m3u_playlist += f'#EXTINF:0 tvg-name="{channel["name"]}" group-title="{group_title}" tvg-logo="{logo_url}",{channel["name"]}\n'
                m3u_playlist += f'{stream_url}\n'

    # Log included groups after filtering
    logger.info(f"Groups included after filtering: {sorted(included_groups)}")
    logger.info(f"Groups excluded after filtering: {sorted(all_groups - included_groups)}")

    # Return the M3U playlist
    return Response(
        m3u_playlist,
        mimetype='audio/x-scpls',
        headers={"Content-Disposition": "attachment; filename=LiveStream.m3u"}
    )

if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0')