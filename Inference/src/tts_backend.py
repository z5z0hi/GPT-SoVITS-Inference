backend_version = "2.4.1 240326"
print(f"Backend version: {backend_version}")

# 在开头加入路径
import os, sys

# 尝试清空含有GPT_SoVITS的路径
for path in sys.path:
    if (path.find(r"GPT_SoVITS") != -1) or (path.find(r"gsv_z") != -1):
        sys.path.remove(path)

now_dir = os.getcwd()
sys.path.append(now_dir)
sys.path.append(os.path.join(now_dir, "GPT_SoVITS"))
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

import soundfile as sf
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import JSONResponse, FileResponse, StreamingResponse
from fastapi.middleware.cors import CORSMiddleware
import tempfile
import uvicorn  
import io
import urllib.parse
import tempfile
import hashlib, json

# 将当前文件所在的目录添加到 sys.path


# 从配置文件读取配置
from Inference.src.config_manager import Inference_Config
inference_config = Inference_Config()

workers = inference_config.workers
tts_host = inference_config.tts_host
tts_port = inference_config.tts_port
default_batch_size = inference_config.default_batch_size
default_word_count = inference_config.default_word_count
enable_auth = inference_config.enable_auth
is_classic = inference_config.is_classic
models_path = inference_config.models_path
if enable_auth:
    users = inference_config.users
certfile = inference_config.certfile
keyfile = inference_config.keyfile

try:
    from GPT_SoVITS.TTS_infer_pack.TTS import TTS
except ImportError:
    is_classic = True
    pass

if not is_classic:
    from Inference.src.TTS_Instance import TTS_instance
    from Inference.src.config_manager import update_character_info,  get_deflaut_character_name
    text_count = {}
    for character in update_character_info()['characters_and_emotions']:
        text_count[character.lower()] = 0
    max_instances = 1
    tts_instances = [TTS_instance() for _ in range(max_instances)]
else:
    from Inference.src.classic_inference.classic_load_infer_info import load_character, character_name, get_wav_from_text_api,  update_character_info
    pass


def generate_audio(cha_name,params):
    tts_instance_id = get_tts_instance_id(cha_name)
    instance = tts_instances[tts_instance_id]
    text_count[instance.character.lower()] += len(params["text"])
    gen = instance.get_wav_from_text_api(**params)
    return gen

def get_tts_instance_id(cha_name=None):
    if cha_name is None or not os.path.exists(os.path.join(models_path, cha_name)):
        if max_instances>1:
            cha_name = get_deflaut_character_name()
        else:
            cha_name = tts_instances[0].character
            return 0
        
    # 还需要修正，哪怕是用lock
    # 寻找一个已经加载的实例，如果没有找到，则返回最少使用的实例
    for tts_instance in tts_instances:
        if tts_instance.character.lower() == cha_name.lower():
            return tts_instances.index(tts_instance)
    
    least_used_instance = min(tts_instances, key=lambda x: text_count[x.character.lower()])
    instance_id = tts_instances.index(least_used_instance)
    
    print(f"Loading character {cha_name}")
    
    least_used_instance.load_character(cha_name)  
    # 调试语句
    for index, tts_instance in enumerate(tts_instances):
        print(f"Instance {index}: {tts_instance.character}, text count: {text_count[tts_instance.character.lower()]}")
    return instance_id


# 存储临时文件的字典
temp_files = {}

# 用于防止重复请求
def generate_file_hash(*args):
    """生成基于输入参数的哈希值，用于唯一标识一个请求"""
    hash_object = hashlib.md5()
    for arg in args:
        hash_object.update(str(arg).encode())
    return hash_object.hexdigest()



app = FastAPI()

# 设置CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)



@app.get('/character_list')
async def character_list():
    res = JSONResponse(update_character_info()['characters_and_emotions'])
    return res

@app.get('/voice/speakers')
async def speakers():
    speaker_dict = update_character_info()['characters_and_emotions']
    name_list = list(speaker_dict.keys())
    speaker_list = [{"id": i, "name": name_list[i], "lang":["zh","en","ja"]} for i in range(len(name_list))]
    res = {
        "VITS": speaker_list,
        "GSVI": speaker_list,
        "GPT-SOVITS": speaker_list
    }
    return JSONResponse(res)
params_config = {}

def get_params_config():
    try:
        with open(os.path.join(os.path.dirname(os.path.dirname(__file__)), "params_config.json"), "r", encoding="utf-8") as f:
            return json.load(f)
    except:
        raise FileNotFoundError("params_config.json not found.")
        
params_config = get_params_config()        


def get_params(data = None):
    def get_param_value(param_config):
        for alias in param_config['alias']:
            if alias in data:
                if param_config['type'] == 'int':
                    return int(data[alias])
                elif param_config['type'] == 'float':
                    return float(data[alias])
                elif param_config['type'] == 'bool':
                    return str(data[alias]).lower() in ('true', '1', 't', 'y', 'yes', "allow", "allowed")
                else:  # 默认为字符串
                    return urllib.parse.unquote(data[alias])
        return param_config['default']
    
    if params_config is None:
        raise FileNotFoundError("params_config.json not found.")
    
    # 参数提取
    text = get_param_value(params_config['text'])
    if text.strip() == "":
        raise ValueError("Text cannot be empty.")
    
    cha_name = get_param_value(params_config['cha_name'])
    speaker_id = get_param_value(params_config['speaker_id'])
    if cha_name is None and speaker_id is not None:
        try:
            cha_name = list(update_character_info()['characters_and_emotions'])[speaker_id]
        except:
            cha_name = None
    

    text_language = get_param_value(params_config['text_language'])
    batch_size = get_param_value(params_config['batch_size'])
    if batch_size is None:
        batch_size = default_batch_size
    speed = get_param_value(params_config['speed'])
    top_k = get_param_value(params_config['top_k'])
    top_p = get_param_value(params_config['top_p'])
    temperature = get_param_value(params_config['temperature'])
    seed = get_param_value(params_config['seed'])
    stream = get_param_value(params_config['stream'])
    
    cut_method = get_param_value(params_config['cut_method'])
    character_emotion = get_param_value(params_config['character_emotion'])
    format = get_param_value(params_config['format'])
    
   
    # 下面是已经获得了参数后进行的操作
    if cut_method == "auto_cut":
        cut_method = f"auto_cut_{default_word_count}"
    
    params = {
        "text": text,
        "text_language": text_language,
        
        "top_k": top_k,
        "top_p": top_p,
        "temperature": temperature,
        "character_emotion": character_emotion,
        "cut_method": cut_method,
        "stream": stream
    }
    # 如果不是经典模式，则添加额外的参数
    if not is_classic:
        params["batch_size"] = batch_size
        params["speed_factor"] = speed
        params["seed"] = seed
    
    
    # if not format in ['wav', 'mp3', 'ogg']:
    #     raise ValueError("Invalid format.")
    
    save_temp = get_param_value(params_config['save_temp'])
    request_hash = generate_file_hash(text, text_language, top_k, top_p, temperature, character_emotion, cha_name, seed)
    
    return params, cha_name, format, save_temp, request_hash, stream



async def tts(request: Request):

    
    # 尝试从JSON中获取数据，如果不是JSON，则从查询参数中获取
    if request.method == "GET":
        data = request.query_params
    else:
        data = await request.json()

        
    try:
        params, cha_name, format, save_temp, request_hash, stream = get_params(data)
    except Exception as e:
        return HTTPException(status_code=400, detail=str(e))
    
    if not is_classic:
        gen = generate_audio(cha_name, params)
    else:
        global character_name
        if cha_name is not None and cha_name != character_name and os.path.exists(os.path.join(models_path, cha_name)):
            character_name = cha_name
            load_character(character_name)
        gen = get_wav_from_text_api(**params)


    if stream == False:
        if save_temp and request_hash in temp_files:
            return FileResponse(path=temp_files[request_hash], media_type=f'audio/{format}')
        else:
            # 假设 gen 是你的音频生成器
            try:
                sampling_rate, audio_data = next(gen)
            except StopIteration:
                raise HTTPException(status_code=404, detail="Generator is empty or error occurred")
            # 创建一个临时文件
            with tempfile.NamedTemporaryFile(delete=False, suffix=f'.{format}') as tmp_file:
                # 尝试写入用户指定的格式，如果失败则回退到 WAV 格式
                try:
                    sf.write(tmp_file, audio_data, sampling_rate, format=format)
                except Exception as e:
                    # 如果指定的格式无法写入，则回退到 WAV 格式
                    sf.write(tmp_file, audio_data, sampling_rate, format='wav')
                    format = 'wav'  # 更新格式为 wav
                
                tmp_file_path = tmp_file.name
                if save_temp:
                    temp_files[request_hash] = tmp_file_path
            # 返回文件响应，FileResponse 会负责将文件发送给客户端
            return FileResponse(tmp_file_path, media_type=f"audio/{format}", filename=f"audio.{format}")
    else:
        
        return StreamingResponse(gen,  media_type='audio/wav')

routes = ['/tts']
try:
    with open(os.path.join(os.path.dirname(os.path.dirname(__file__)), "params_config.json"), "r", encoding="utf-8") as f:
        config = json.load(f)
        routes = config.get("route", {}).get("alias", ['/tts'])
except:
    pass

# 注册路由
for path in routes:
    app.api_route(path, methods=['GET', 'POST'])(tts)

# 便于小白理解
def print_ipv4_ip(host = "127.0.0.1", port = 5000):
    import socket

    def get_internal_ip():
        """获取内部IP地址"""
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            # 这不会发送真正的数据包
            s.connect(('10.253.156.219', 1))
            IP = s.getsockname()[0]
        except Exception:
            IP = '127.0.0.1'
        finally:
            s.close()
        return IP

    if host == "0.0.0.0":
        display_hostname = get_internal_ip()
        if display_hostname != "127.0.0.1":
            print(f"Please use http://{display_hostname}:{port} to access the service.")

if __name__ == "__main__":
    print_ipv4_ip(tts_host, tts_port)
    uvicorn.run(app, host=tts_host, port=tts_port,
                ssl_certfile=certfile,
                ssl_keyfile=keyfile
                )


