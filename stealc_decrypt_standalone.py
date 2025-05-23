# Author: RussianPanda
# Sample: 911981d657b02f2079375eecbd81f3d83e5fa2b8de73afad21783004cbcc512d

import re
import base64
import argparse
import string
import json


from typing import BinaryIO, List, Optional
from maco.extractor import Extractor
from maco.model import ConnUsageEnum, ExtractorModel, CategoryEnum
from maco import model
from validators import url, ipv4, domain
import validators
from urllib.parse import urlparse

def rc4_ksa(key):
    S = list(range(256))
    j = 0
    for i in range(256):
        j = (j + S[i] + key[i % len(key)]) % 256
        S[i], S[j] = S[j], S[i]
    return S

def rc4_prga(S, data):
    i = 0
    j = 0
    result = bytearray()
    
    for byte in data:
        i = (i + 1) % 256
        j = (j + S[i]) % 256
        S[i], S[j] = S[j], S[i]
        k = S[(S[i] + S[j]) % 256]
        result.append(byte ^ k)
    
    return bytes(result)

def rc4_decrypt(data, key):
    if isinstance(key, str):
        key = key.encode('utf-8')
    
    if isinstance(data, str):
        try:
            data = bytes.fromhex(data)
        except:
            data = data.encode('utf-8')
    
    S = rc4_ksa(key)
    return rc4_prga(S, data)

def find_and_decrypt_strings(binary_data, rc4_key):
    printable_pattern = rb'(?:[\x20-\x7E]{4,})'
    
    base64_pattern = re.compile(b'[A-Za-z0-9+/=]{4,}')
    
    special_pattern = re.compile(rb'/[A-Za-z0-9+/]{4,}=*')
    
    potential_strings = []
    
    for match in re.finditer(printable_pattern, binary_data):
        potential_strings.append(match.group(0))
    
    for match in re.finditer(base64_pattern, binary_data):
        if match.group(0) not in potential_strings:
            potential_strings.append(match.group(0))
    
    for match in re.finditer(special_pattern, binary_data):
        if match.group(0) not in potential_strings:
            potential_strings.append(match.group(0))
    
    results = []
    for encrypted_bytes in potential_strings:
        try:
            encrypted = encrypted_bytes.decode('utf-8', errors='ignore')
            
            decrypted = rc4_decrypt(encrypted_bytes, rc4_key)
            
            try:
                decrypted_str = decrypted.decode('utf-8', errors='replace')
            except:
                decrypted_str = str(decrypted)
            
            base64_decrypted_str = None
            if re.match(r'^[A-Za-z0-9+/]*={0,2}$', encrypted) and len(encrypted) % 4 == 0:
                try:
                    decoded = base64.b64decode(encrypted_bytes)
                    base64_decrypted = rc4_decrypt(decoded, rc4_key)
                    
                    try:
                        base64_decrypted_str = base64_decrypted.decode('utf-8', errors='replace')
                    except:
                        base64_decrypted_str = str(base64_decrypted)
                except:
                    base64_decrypted_str = None
            
            if encrypted.startswith('/'):
                try:
                    clean_str = encrypted[1:]
                    padding_needed = len(clean_str) % 4
                    if padding_needed:
                        clean_str += '=' * (4 - padding_needed)
                    
                    decoded = base64.b64decode(clean_str)
                    special_decrypted = rc4_decrypt(decoded, rc4_key)
                    
                    try:
                        special_decrypted_str = special_decrypted.decode('utf-8', errors='replace')
                        if base64_decrypted_str is None:
                            base64_decrypted_str = special_decrypted_str
                    except:
                        pass
                except:
                    pass
            
            results.append({
                'encrypted': encrypted,
                'direct_decrypted': decrypted_str,
                'base64_decrypted': base64_decrypted_str
            })
        except Exception as e:
            continue
    
    return results

def is_valid_string(s, min_length=4):
    if len(s) < min_length:
        return False
    
    if re.match(r'^\d+\.\d+\.\d+\.\d+$', s):
        return True
    
    if any(c.isalpha() for c in s):
        return True
    
    if s.isdigit() and len(s) >= 4:
        return True
    
    return False

def find_opcode(binary_data):
    opcode = bytes.fromhex("73 74 72 69 6E 67 20 74 6F 6F 20 6C 6F 6E 67")
    
    positions = []
    pos = binary_data.find(opcode)
    
    while pos != -1:
        positions.append(pos)
        pos = binary_data.find(opcode, pos + 1)
    
    if positions:
        build_id = None
        rc4_key = None
        rc4_traffic = None
        
        for pos in positions:
            next_bytes = binary_data[pos + len(opcode):pos + len(opcode) + 200]
            
            current_str = ""
            build_id_end = 0
            for i, b in enumerate(next_bytes):
                if 32 <= b <= 126:
                    current_str += chr(b)
                elif current_str:
                    build_id = current_str.strip()  # Strip spaces from the build ID
                    build_id_end = i
                    break
            
            if not build_id:
                continue
                
            string_count = 0
            current_str = ""
            for i, b in enumerate(next_bytes[build_id_end:]):
                if 32 <= b <= 126:
                    current_str += chr(b)
                elif current_str:
                    string_count += 1
                    if string_count == 2:
                        rc4_key = current_str
                        break
                    current_str = ""
            
            zeroes_start = None
            zeroes_count = 0
            for i in range(build_id_end, len(next_bytes)):
                if next_bytes[i] == 0:
                    if zeroes_start is None:
                        zeroes_start = i
                    zeroes_count += 1
                    if zeroes_count >= 5:
                        break
                else:
                    zeroes_start = None
                    zeroes_count = 0
            
            if zeroes_start and zeroes_count >= 5:
                traffic_start = zeroes_start + zeroes_count
                if traffic_start < len(next_bytes):
                    traffic_bytes = []
                    for i in range(traffic_start, len(next_bytes)):
                        if next_bytes[i] == 0:
                            break
                        traffic_bytes.append(next_bytes[i])
                    
                    if traffic_bytes:
                        try:
                            rc4_traffic = bytes(traffic_bytes).decode('ascii', errors='replace')
                        except:
                            rc4_traffic = ''.join(chr(b) if 32 <= b <= 126 else f'\\x{b:02x}' for b in traffic_bytes)
            
            if build_id and rc4_key and rc4_traffic:
                break
        
        return {
            "build_id": build_id,
            "rc4_key": rc4_key,
            "rc4_traffic": rc4_traffic
        }
    else:
        return None

def find_c2(decrypted_strings):
    ip_pattern = re.compile(r'^\d+\.\d+\.\d+\.\d+$')
    path_pattern = re.compile(r'^/[a-zA-Z0-9._/-]+\.php$')
    
    ip_address = None
    path = None
    domain_found = None
    
    for s in decrypted_strings:
        if ip_pattern.match(s):
            ip_address = s
            print(f"ip_addr found: {ip_address}")
            break
    
    if ip_address:
        ip_index = decrypted_strings.index(ip_address)
        for i in range(ip_index + 1, min(ip_index + 5, len(decrypted_strings))):
            if i < len(decrypted_strings) and path_pattern.match(decrypted_strings[i]):
                path = decrypted_strings[i]
                break
    # c2 tends to be one before the path
    for s in decrypted_strings:
        if path_pattern.match(s):
            path = s
            path_index = decrypted_strings.index(s)
            if validators.domain(decrypted_strings[path_index - 1]):
                domain_found = decrypted_strings[path_index - 1]
                print(f"domain found: {domain_found}")
            break

    if ip_address and path:
        return f"https://{ip_address}{path}"
    elif ip_address:
        return ip_address
    elif domain_found and path:
        return f"https://{domain_found}{path}"
    elif domain_found:
        return domain_found
    else:
        return None

def main():
    parser = argparse.ArgumentParser(description='Find and decrypt strings in binary files')
    parser.add_argument('file', help='Binary file to analyze')
    parser.add_argument('--min-length', type=int, default=4, help='Minimum length for decrypted strings (default: 4)')
    parser.add_argument('--key', help='Specify RC4 key manually (optional)')
    args = parser.parse_args()
    
    try:
        with open(args.file, 'rb') as f:
            binary_data = f.read()
    except FileNotFoundError:
        print(f"Error: File '{args.file}' not found.")
        return
    
    detected_info = find_opcode(binary_data)
    
    rc4_key = None
    if detected_info and detected_info["rc4_key"]:
        rc4_key = detected_info["rc4_key"]
        print(f"[+] Detected RC4 key: {rc4_key}")
        if detected_info["rc4_traffic"]:
            print(f"[+] Detected RC4 traffic key: {detected_info['rc4_traffic']}")
    elif hasattr(args, 'key') and args.key:
        rc4_key = args.key
        print(f"[+] Using provided RC4 key: {rc4_key}")
    else:
        print("[-] No RC4 key detected or provided. Cannot decrypt.")
        return
    
    results = find_and_decrypt_strings(binary_data, rc4_key)
    
    printable_chars = set(string.printable)
    
    unique_decrypted = []
    seen = set()
    
    for result in results:
        if 'base64_decrypted' in result and result['base64_decrypted']:
            decrypted = result['base64_decrypted']
            if (all(c in printable_chars for c in decrypted) and 
                is_valid_string(decrypted, args.min_length) and
                decrypted not in seen):
                seen.add(decrypted)
                unique_decrypted.append(decrypted)
        
        elif 'direct_decrypted' in result:
            decrypted = result['direct_decrypted']
            if (all(c in printable_chars for c in decrypted) and 
                is_valid_string(decrypted, args.min_length) and
                decrypted not in seen):
                seen.add(decrypted)
                unique_decrypted.append(decrypted)
    
    c2_url = find_c2(unique_decrypted)
    
    output_data = {
        "metadata": {
            "build_id": detected_info["build_id"] if detected_info else None,
            "rc4_key": rc4_key,
            "rc4_traffic": detected_info["rc4_traffic"] if detected_info else None,
            "c2": c2_url
        },
        "decrypted_strings": unique_decrypted
    }
    
    print(json.dumps(output_data, indent=4))

class Stealc(Extractor):
    family = "Stealc"
    author = "@RussianPanda"
    last_modified = "2025-04-25"
    sharing: str = "TLP:CLEAR"
    yara_rule: str = """
rule win_mal_StealC_v2 {
    meta:
        id = "23AhaIHSlcGvXYhsbeFLQ2"
        fingerprint = "1715ef4e1914a50d8f4a0644ddfd7f9bb2b6f0ec0dfc77615dce4dd5fc943166"
        version = "1.0"
        modified = "2025-04-14"
        status = "RELEASED"
        sharing = "TLP:WHITE"
        source = "RUSSIANPANDA"
        author = "RussianPanda"
        description = "Detects StealC v2"
        category = "MALWARE"
        malware = "STEALC"
        malware_type = "INFOSTEALER"
        report = "HTTPS://TRAC-LABS.COM/AUTOPSY-OF-A-FAILED-STEALER-STEALC-V2-A4E32DA04396"
        hash = "bc7e489815352f360b6f0c0064e1d305db9150976c4861b19b614be0a5115f97"
        original_date = "4/10/2025"

    strings:
        $s1 = {48 8d ?? ?? ?? ??  00 48 8d}
        $s2 = {0F B7 C8 81 E9 19 04 00 00 74 14 83 E9 09 74 0F 83 E9 01 74 0A 83 E9 1C 74 05 83 F9 04 75 08}
    condition:
        uint16(0) == 0x5A4D and #s1 > 500 and all of them and filesize < 900KB
}
"""
    def run(self, stream: BinaryIO, matches: List = []) -> Optional[ExtractorModel]:
        # Reset printed_configs per each run to prevent config leaks onto other samples
        global printed_configs
        printed_configs = set()

        file_data = stream.read()
        #sha256_hash = hashlib.sha256(file_data).hexdigest()
        #self.logger.info(f"SHA-256: {sha256_hash}")
        config = ExtractorModel(family=self.family)
        config.category = [
            CategoryEnum.infostealer
        ]
        # minimum length for decrypted strings
        min_length : int = 4

        detected_info = find_opcode(file_data)
        
        rc4_key = None
        if detected_info and detected_info["rc4_key"]:
            rc4_key = detected_info["rc4_key"]
            print(f"[+] Detected RC4 key: {rc4_key}")
            if detected_info["rc4_traffic"]:
                print(f"[+] Detected RC4 traffic key: {detected_info['rc4_traffic']}")
        else:
            print("[-] No RC4 key detected or provided. Cannot decrypt.")
            raise RuntimeError("[-] No RC4 key detected. Cannot decrypt.")
        
        results = find_and_decrypt_strings(file_data, rc4_key)
        
        printable_chars = set(string.printable)
        
        unique_decrypted = []
        seen = set()
        
        for result in results:
            if 'base64_decrypted' in result and result['base64_decrypted']:
                decrypted = result['base64_decrypted']
                if (all(c in printable_chars for c in decrypted) and 
                    is_valid_string(decrypted, min_length) and
                    decrypted not in seen):
                    seen.add(decrypted)
                    unique_decrypted.append(decrypted)
            
            elif 'direct_decrypted' in result:
                decrypted = result['direct_decrypted']
                if (all(c in printable_chars for c in decrypted) and 
                    is_valid_string(decrypted, min_length) and
                    decrypted not in seen):
                    seen.add(decrypted)
                    unique_decrypted.append(decrypted)
        
        c2_url = find_c2(unique_decrypted)
        
        output_data = {
                "metadata": {
                    "build_id": detected_info["build_id"] if detected_info else None,
                    "rc4_key": rc4_key,
                    "rc4_traffic": detected_info["rc4_traffic"] if detected_info else None,
                    "c2": c2_url
                },
                "decrypted_strings": unique_decrypted
            }
        
        print(json.dumps(output_data, indent=4))

        config.encryption.append(
            ExtractorModel.Encryption(
                algorithm="RC4",
                public_key=rc4_key,
                mode="stream",
                usage=model.ExtractorModel.Encryption.UsageEnum.config
            )
        )

        c2_http = ExtractorModel.Http
        
        if url(c2_url):
            parsed_url = urlparse(c2_url)
            config.http.append(c2_http(
                protocol = parsed_url.scheme,
                usage = "c2",
                uri = c2_url,
                path = parsed_url.path,
                query = parsed_url.query,
                hostname = parsed_url.hostname
                )
            )
        if ipv4(c2_url):
            connect = ExtractorModel.Http(
            hostname=c2_url,
            usage=ConnUsageEnum.c2,
            )
            config.http.append(connect)
        if domain(c2_url):
            connect = ExtractorModel.Http(
            hostname=c2_url,
            usage=ConnUsageEnum.c2,
            )
            config.http.append(connect)
        build_id = output_data['metadata']['build_id'].strip()
        if build_id is not None:
            config.campaign_id = [build_id]
            config.other['build_id'] = build_id
        if output_data['decrypted_strings']:
            config.decoded_strings = output_data['decrypted_strings']
        config.version = "2"
        return config
    
if __name__ == "__main__":
    main()
