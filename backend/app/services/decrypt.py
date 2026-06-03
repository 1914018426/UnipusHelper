from Crypto.Cipher import AES
from Crypto.Util.Padding import unpad
import binascii
import json
import re

# 不支持的题型
INVALID_TYPES = {'DISCUSSION', 'MULTI_FILE_UPLOAD', 'EXIT_TICKET', 'MULTICHOICE', 'UNKNOWN'}
# 预设题型（返回空答案提交体）
PRESET_MODES = {'VIDEO', 'AUDIO', 'READING'}
# 学习类题型（submitType=2，否则=1）
STUDY_MODES = {'VOCABULARY', 'TEXT_LEARN', 'STUDY', 'RICH_TEXT_READ', 'VIDEO_POPUP', 'DISCUSSION', 'INPUT', 'VIDEO_POINT_READ'}

def normalize_base_type(raw):
    if not raw or not isinstance(raw, str):
        return 'UNKNOWN'
    return re.sub(r'([a-z])([A-Z])', r'\1_\2', raw.strip()).replace('-', '_').upper()

def is_supported(raw_type):
    t = normalize_base_type(raw_type)
    return t not in INVALID_TYPES

def get_submit_type(base_type_raw):
    t = normalize_base_type(base_type_raw)
    if t in INVALID_TYPES:
        raise ValueError(f'Unsupported BaseType: {t}')
    return 2 if t in STUDY_MODES else 1

def decrypt_aes(data_with_prefix, key_suffix):
    if not data_with_prefix or not data_with_prefix.startswith('unipus.'):
        return data_with_prefix

    try:
        hex_str = data_with_prefix[len('unipus.'):]
        key = ('1a2b3c4d' + key_suffix).encode('utf-8')
        # 确保key为16字节
        key = key[:16].ljust(16, b'\0')

        ciphertext = binascii.unhexlify(hex_str)
        cipher = AES.new(key, AES.MODE_ECB)
        decrypted = cipher.decrypt(ciphertext)
        # 尝试PKCS7去填充
        try:
            decrypted = unpad(decrypted, AES.block_size)
        except ValueError:
            pass
        return decrypted.decode('utf-8', errors='ignore').replace('\x00', '').strip()
    except Exception as e:
        print(f"[Decrypt Error] {e}")
        return ""

def extract_answers(decrypted_str):
    if not decrypted_str:
        return None, []
    try:
        data_list = json.loads(decrypted_str)
        if not isinstance(data_list, list) or len(data_list) == 0:
            return None, []

        first_item = data_list[0]
        instance_id = str(first_item.get('id')) if first_item.get('id') else None
        answers = []

        if first_item.get('answer'):
            answer_str = first_item['answer']
            # 处理多层转义
            answer_str = answer_str.replace('\\\\', '\\')
            answer_obj = json.loads(answer_str) if isinstance(answer_str, str) else answer_str
            for child in answer_obj.get('children', []):
                if isinstance(child.get('answers'), list) and len(child['answers']) > 0:
                    answers.append(child['answers'][0])

        return instance_id, answers
    except Exception as e:
        print(f"[Extract Answers Error] {e}")
        return None, []

def build_submit_body(instance_id, answers, group_id, course_id, open_id, question_type):
    t = normalize_base_type(question_type)

    if t in INVALID_TYPES:
        raise ValueError(f'Unsupported question type: {question_type}')

    # 预设题型返回空答案提交体
    if t in PRESET_MODES:
        return json.dumps({
            "quesDatas": [],
            "groupId": group_id,
            "isCompleted": [],
            "thirdPartyJudges": "[]",
            "submitType": 2,
            "hideLoading": True,
            "associationGroupId": "",
            "courseId": course_id,
            "openId": open_id,
            "version": "default"
        }, ensure_ascii=False)

    # 构造正常题型的请求体
    ques_datas = []
    is_completed = []
    third_party_judges = []
    children = []

    for answ in answers:
        clean_answ = re.sub(r'[^\w\s]', '', answ).strip()
        children.append({
            "value": [clean_answ],
            "isDone": True
        })
        third_party_judges.append({
            "value": clean_answ,
            "question_type": map_question_type(question_type),
            "payloads": [{
                "recordDetail": {
                    "score": 100.00,
                    "comment": ""
                }
            }]
        })
        is_completed.append(True)

    answer_obj = {
        "value": [],
        "children": children,
        "progress": {},
        "record": {"url": ""}
    }

    ques_data = {
        "instanceId": instance_id or "123456",
        "answer": json.dumps(answer_obj, ensure_ascii=False),
        "context": json.dumps({"state": "submitted"}, ensure_ascii=False),
        "contextVersion": 0,
        "answerVersion": 0
    }
    ques_datas.append(ques_data)

    submit_type = 2 if t in STUDY_MODES else 1

    body = {
        "quesDatas": ques_datas,
        "groupId": group_id,
        "isCompleted": is_completed,
        "thirdPartyJudges": json.dumps(third_party_judges, ensure_ascii=False),
        "submitType": submit_type,
        "hideLoading": False,
        "associationGroupId": "",
        "courseId": course_id,
        "openId": open_id,
        "version": "default"
    }

    return json.dumps(body, ensure_ascii=False)

def map_question_type(qtype):
    name = qtype.lower()
    if 'banked' in name or 'cloze' in name:
        return "material-banked-cloze"
    return name.replace('_', '-')
