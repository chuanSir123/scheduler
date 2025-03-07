from typing import Any, Dict, List, Optional,Annotated
import asyncio
from kirara_ai.workflow.core.block import Block, Input, Output, ParamMeta
from .scheduler import TaskScheduler
from kirara_ai.llm.format.message import LLMChatMessage
from kirara_ai.llm.format.response import LLMChatResponse
from kirara_ai.ioc.container import DependencyContainer
from kirara_ai.im.adapter import IMAdapter
from kirara_ai.im.manager import IMManager
from kirara_ai.im.message import IMMessage, MessageElement, TextMessage, ImageMessage, VoiceMessage, MediaMessage,VideoElement
from kirara_ai.im.sender import ChatSender
from kirara_ai.llm.llm_manager import LLMManager
from kirara_ai.llm.llm_registry import LLMAbility
from kirara_ai.llm.format.request import LLMChatRequest
import json
import re
from .storage import TaskStorage
import os
from kirara_ai.logger import get_logger
from kirara_ai.workflow.core.block.registry import BlockRegistry
import requests
from kirara_ai.plugin_manager.plugin_loader import PluginLoader
from urllib.parse import urlparse, unquote

def im_adapter_options_provider(container: DependencyContainer, block: Block) -> List[str]:
    return [key for key, _ in container.resolve(IMManager).adapters.items()]

class CreateTaskBlock(Block):
    """定时任务Block"""
    name = "create_task"
    description = "创建定时任务"
    container: DependencyContainer
    inputs = {
        "cron": Input(name="定时任务cron表达式",label="cron", data_type= str, description="定时任务cron表达式"),
        "task_content": Input(name="定时任务内容",label="定时任务内容", data_type= str, description="定时任务内容"),
        "target": Input(
                    "target",
                    "发送对象",
                    ChatSender,
                    "要发送给谁，如果填空则默认发送给消息的发送者",
                    nullable=True,
                ),
    }

    outputs = {
        "results": Output(name="results",label="定时任务执行结果",data_type= str, description="定时任务执行结果")
    }

    def __init__(self,
        im_name: Annotated[Optional[str], ParamMeta(label="聊天平台适配器名称", options_provider=im_adapter_options_provider)] = None):
        self.im_name = im_name
        self.logger = get_logger("SchedulerBlock")

        self.scheduler = None

    def execute(self,cron=None,task_content="",target=None) -> Dict[str, Any]:
        self.scheduler = self.container.resolve(PluginLoader).plugins["scheduler"].scheduler
        try:
            if not self.im_name:
                adapter = self.container.resolve(IMAdapter)
            else:
                adapter = self.container.resolve(IMManager).get_adapter(self.im_name)
            self.scheduler.adapter = adapter
            self.scheduler.adapter_name = self.im_name or "onebot"
            # 在新线程中创建事件循环
            try:
                loop = asyncio.get_event_loop()
            except RuntimeError:
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)

            task = loop.run_until_complete(
                self.scheduler.create_task(
                    cron=cron,
                    task_content=task_content,
                    target=target or self.container.resolve(IMMessage).sender,
                )
            )
            # 格式化任务信息为字符串
            task_info = f"任务ID: {task.id}\n下次执行时间: {task.next_run_time}\n任务内容: {task.task_content}"
            return {"results": f"\n已创建定时任务:\n{task_info}"}
        except Exception as e:
            print(e)
            return {"results": f"创建任务失败: {str(e)}"}

def model_name_options_provider(container: DependencyContainer, block: Block) -> List[str]:
    llm_manager: LLMManager = container.resolve(LLMManager)
    return llm_manager.get_supported_models(LLMAbility.TextChat)

class CreateOneTimeTaskBlock(Block):
    """一次性定时任务Block"""
    name = "create_one_time_task"
    description = "创建一次性定时任务"
    container: DependencyContainer
    inputs = {
        "minutes": Input(name="延迟时间",label="minutes", data_type=int, description="多少分钟后执行任务"),
        "task_content": Input(name="定时任务内容",label="定时任务内容", data_type=str, description="定时任务内容"),
        "target": Input(
                    "target",
                    "发送对象",
                    ChatSender,
                    "要发送给谁，如果填空则默认发送给消息的发送者",
                    nullable=True,
                ),
    }

    outputs = {
        "results": Output(name="results",label="定时任务执行结果",data_type=str, description="定时任务执行结果")
    }

    def __init__(self,
        im_name: Annotated[Optional[str], ParamMeta(label="聊天平台适配器名称", options_provider=im_adapter_options_provider)] = None):
        self.im_name = im_name
        self.logger = get_logger("SchedulerBlock")
        self.scheduler = None

    def execute(self, minutes: int = None, task_content: str="", target: Optional[ChatSender] = None) -> Dict[str, Any]:
        try:

            self.scheduler = self.container.resolve(PluginLoader).plugins["scheduler"].scheduler
            if not self.im_name:
                adapter = self.container.resolve(IMAdapter)
            else:
                adapter = self.container.resolve(IMManager).get_adapter(self.im_name)
            self.scheduler.adapter = adapter

            try:
                loop = asyncio.get_event_loop()
            except RuntimeError:
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)

            task = loop.run_until_complete(
                self.scheduler.create_one_time_task(
                    minutes=minutes,
                    task_content=task_content,
                    target=target or self.container.resolve(IMMessage).sender,
                )
            )
            task_info = f"任务ID: {task.id}\n执行时间: {task.next_run_time}\n任务内容: {task.task_content}"
            return {"results": f"\n已创建一次性定时任务:\n{task_info}"}
        except Exception as e:
            print(e)
            return {"results": f"创建任务失败: {str(e)}"}

class AutoExecuteTools(Block):
    name = "auto_execute_tools"
    inputs = {
        "prompt": Input("prompt", "LLM 对话记录", List[LLMChatMessage], "用于解析定时任务的对话记录")
    }
    outputs = {
        "results": Output("results", "执行结果", str, "执行结果")
    }
    container: DependencyContainer

    def __init__(
        self,
        model_name: Annotated[
            Optional[str],
            ParamMeta(label="模型 ID", description="要使用的模型 ID", options_provider=model_name_options_provider),
        ] = None,
        max_retries: int = 3,
        available_block_names: str = "create_task,create_one_time_task,get_tasks,delete_task,delete_all_tasks,image_generate,web_search,music_search"
    ):
        self.model_name = model_name
        self.max_retries = max_retries
        self.logger = get_logger("findSchedulerCron")
        self.scheduler = None

        # 获取所有可用的block名称列表
        self.block_names = [name.strip() for name in available_block_names.split(",")]

        # 初始化可用的blocks字典
        self.available_blocks = {}

    def fillParams(self, action: Dict, all_results: List[str], llm_manager) -> Dict:
        """使用LLM根据先前的执行结果填充行动参数

        Args:
            action: 原始行动字典，包含可能需要填充的参数
            all_results: 之前行动的执行结果列表
            llm_manager: LLM管理器实例

        Returns:
            更新后的行动字典
        """
        if not all_results:  # 如果没有之前的结果，直接返回原始行动
            return action

        action_name = action.get("action", "")
        params = action.get("params", {})

        # 获取该行动类型需要的参数说明
        param_descriptions = {}
        if action_name in self.available_blocks:
            block_class = self.available_blocks[action_name]
            for param_name, input_def in block_class.inputs.items():
                param_descriptions[param_name] = {
                    "description": input_def.description,
                    "type": str(input_def.data_type.__name__),
                    "required": not input_def.nullable if hasattr(input_def, 'nullable') else True
                }

        # 预先构建可能导致问题的字符串
        json_format_example = '{"param1": "value1", "param2": "value2", ...}'
        separator = "-" * 30
        # 将all_results拼接到一个字符串中，避免在f-string中使用\n
        previous_results = "\n".join(all_results)

        # 使用拼接的部分构建system_prompt
        system_prompt = f"""你是一个参数填充助手。请根据之前操作的结果，填充当前操作所需的参数。

当前操作: {action_name}
参数说明: {json.dumps(param_descriptions, ensure_ascii=False)}
原始参数: {json.dumps(params, ensure_ascii=False)}

之前操作的结果:
{separator}
{previous_results}
{separator}

请分析之前的结果，并填充当前操作所需的参数。只返回一个JSON对象，包含所有必要的参数，格式如下:
{json_format_example}

注意:
1. 只返回JSON对象，不要添加任何解释或额外文本
2. 保留原始参数中已有的值，除非它们需要根据上下文进行更新
3. 确保参数类型与参数说明中的要求匹配
"""

        model_id = self.model_name or llm_manager.get_llm_id_by_ability(LLMAbility.TextChat)
        llm = llm_manager.get_llm(model_id)

        messages = [LLMChatMessage(role="user", content=system_prompt)]
        req = LLMChatRequest(messages=messages, model=model_id)

        # 重试逻辑
        for retry in range(self.max_retries):
            try:
                response = llm.chat(req).choices[0].message.content
                json_match = re.search(r'(\{[\s\S]*?\})', response)
                filled_params =  json.loads(json_match.group(1))

                if filled_params:  # 如果成功解析到参数
                    # 合并原始参数和填充的参数
                    updated_action = action.copy()
                    updated_action["params"] = {**params, **filled_params}
                    return updated_action

                self.logger.warning(f"Retry {retry + 1}/{self.max_retries}: Failed to get valid parameters")
            except Exception as e:
                self.logger.error(f"Retry {retry + 1}/{self.max_retries}: Error during parameter filling: {str(e)}")
                if retry == self.max_retries - 1:  # 最后一次重试失败
                    break
                continue

        # 如果所有重试都失败，返回原始行动
        self.logger.warning("All parameter filling retries failed, using original parameters")
        return action
    def _try_parse_json_array(self, response: str) -> List[Dict]:
        """尝试从响应中解析JSON数组"""
            # 匹配方括号中的所有内容


    def execute(self, prompt: List[LLMChatMessage]) -> Dict[str, Any]:
        self.scheduler = self.container.resolve(PluginLoader).plugins["scheduler"].scheduler
        self.scheduler.adapter = self.container.resolve(IMAdapter)
        llm_manager = self.container.resolve(LLMManager)
        registry: BlockRegistry = self.container.resolve(BlockRegistry)
        for block in registry.get_all_types():
            if block.name in self.block_names:
                self.available_blocks[block.name] = block
        model_id = self.model_name or llm_manager.get_llm_id_by_ability(LLMAbility.TextChat)
        if not model_id:
            raise ValueError("No available LLM models found")

        llm = llm_manager.get_llm(model_id)
        if not llm:
            raise ValueError(f"LLM {model_id} not found, please check the model name")

        # 构建操作说明数组
        available_actions = []
        for action_name, block in self.available_blocks.items():
            action_info = {"action": action_name,"description": block.description if hasattr(block, 'description') else "","params": {}}

            # 从block的inputs中获取参数说明
            for param_name, input_def in block.inputs.items():
                action_info["params"][param_name] = {"description": input_def.description,"type": str(input_def.data_type.__name__),"required": not input_def.nullable if hasattr(input_def, 'nullable') else True}

            available_actions.append(action_info)

        system_prompt = f"""你是一个任务解析助手。你需要从用户的对话中理解用户意图并返回相应的操作链（可能包含多个任务）。

可用的操作类型和参数如下：
{json.dumps(available_actions, ensure_ascii=False)}

请按照以下JSON格式返回结果：[{{"action": "<操作名称>","params": {{"<参数名>": "<参数值>"}}}}]

注意：
1. 如果无法理解用户意图，请返回：[]
2. params中只需要包含对应action所需的参数
3. 请直接返回json数组格式数据
"""

        messages = [
            LLMChatMessage(role="system", content=system_prompt),
            *prompt
        ]

        req = LLMChatRequest(messages=messages, model=model_id)

        # 重试逻辑
        for retry in range(self.max_retries):
            try:
                response = llm.chat(req).choices[0].message.content
                json_match = re.search(r'(\[[\s\S]*\])', response)
                actions =  json.loads(json_match.group(1))
                break
            except Exception as e:
                self.logger.error(f"Retry {retry + 1}/{self.max_retries}: Error during LLM chat: {str(e)}")
                if retry == self.max_retries - 1:  # 最后一次重试失败
                    return {"results": f"执行失败: {str(e)}"}
                continue

        # 执行所有解析出的操作
        all_results = []
        lastAction = ""
        for i, action in enumerate(actions):
            try:
                # 对非第一个action，使用fillParams方法填充参数
                if i > 0 and action.get("params", {}) and action.get("action") != lastAction:
                    action = self.fillParams(action, all_results, llm_manager)
                lastAction = action.get("action")
                action_name = action.get("action")
                params = action.get("params", {})

                if action_name not in self.available_blocks:
                    all_results.append(f"未知操作: {action_name}")
                    continue

                # 为block注入container
                block_class = self.available_blocks[action_name]
                block_instance = block_class()  # Create an instance of the block
                block_instance.container = self.container

                # 执行block并收集结果
                execution_result = block_instance.execute(**params)

                # 修改这里：收集所有输出值而不仅仅是"results"
                if isinstance(execution_result, dict):
                    # 将字典转换为 key:value 格式的字符串
                    result_str = "\n".join([f"{k}:{v}" for k, v in execution_result.items()])
                    all_results.append(result_str)
                else:
                    all_results.append(str(execution_result).strip())
            except Exception as e:
                all_results.append(f"执行 {action_name} 失败: {str(e)}")

        # 返回所有结果
        return {"results": ("你的工具调用运行结果："+"\n".join(all_results)+"\n") if all_results else "没有工具调用\n"}

class GetTasksBlock(Block):
    """获取定时任务Block"""
    name = "get_tasks"
    description = "获取定时任务"
    container: DependencyContainer
    inputs = {
        "target": Input(
            "target",
            "发送对象",
            ChatSender,
            "要获取哪个聊天的任务，为空则获取所有任务",
            nullable=True
        )
    }
    outputs = {
        "results": Output(
            name="results",
            label="任务列表",
            data_type=str,
            description="定时任务列表"
        )
    }

    def __init__(self):
        self.logger = get_logger("GetTasksBlock")
        self.scheduler = None


    def execute(self, target: Optional[ChatSender] = None) -> Dict[str, Any]:
        try:
            self.scheduler = self.container.resolve(PluginLoader).plugins["scheduler"].scheduler
            chat_id = str(target) if target else  str(self.container.resolve(IMMessage).sender)
            tasks = self.scheduler.get_all_tasks(chat_id)
            if not tasks:
                return {"results": "没有找到任何定时任务"}

            # 格式化任务信息
            task_info = []
            for task in tasks:
                info = (
                    f"任务ID: {task.id}\n"
                    f"Cron表达式: {task.cron if not task.is_one_time else '一次性任务'}\n"
                    f"下次执行时间: {task.next_run_time}\n"
                    f"任务内容: {task.task_content}\n"
                    f"聊天ID: {task.chat_id}\n"
                    f"------------------------"
                )
                task_info.append(info)

            return {"results": "\n".join(task_info)}
        except Exception as e:
            return {"results": f"获取任务失败: {str(e)}"}

class DeleteTaskBlock(Block):
    """删除定时任务Block"""
    name = "delete_task"
    description = "通过id删除定时任务"
    container: DependencyContainer
    inputs = {
        "task_id": Input(
            name="task_id",
            label="任务ID",
            data_type=str,
            description="要删除的任务ID"
        )
    }
    outputs = {
        "results": Output(
            name="results",
            label="删除结果",
            data_type=str,
            description="删除任务的结果"
        )
    }

    def __init__(self):
        self.logger = get_logger("DeleteTaskBlock")
        self.scheduler = None

    def execute(self, task_id: str = None) -> Dict[str, Any]:

        self.scheduler = self.container.resolve(PluginLoader).plugins["scheduler"].scheduler
        try:
            # 先检查任务是否存在且属于该聊天
            task = self.scheduler.get_task(task_id)
            if not task:
                return {"results": f"任务 {task_id} 不存在"}


            success = self.scheduler.delete_task(task_id)
            if success:
                return {"results": f"成功删除任务"}
            else:
                return {"results": f"删除任务失败，任务 {task_id} 可能不存在"}
        except Exception as e:
            return {"results": f"删除任务失败: {str(e)}"}

class DeleteAllTasksBlock(Block):
    """删除所有定时任务Block"""
    name = "delete_all_tasks"
    description = "删除所有定时任务"
    container: DependencyContainer
    inputs = {
        "target": Input(
            "target",
            "发送对象",
            ChatSender,
            "要删除哪个聊天的所有任务，为空则删除所有任务",
            nullable=True
        )
    }
    outputs = {
        "results": Output(
            name="results",
            label="删除结果",
            data_type=str,
            description="删除任务的结果"
        )
    }

    def __init__(self):
        self.logger = get_logger("DeleteAllTasksBlock")
        self.scheduler = None

    def execute(self, target: Optional[ChatSender] = None) -> Dict[str, Any]:


        self.scheduler = self.container.resolve(PluginLoader).plugins["scheduler"].scheduler
        try:
            chat_id = str(target) if target else str(self.container.resolve(IMMessage).sender)
            success = self.scheduler.delete_all_task(chat_id)
            if success:
                return {"results": f"成功删除所有定时任务"}
            else:
                return {"results": "删除任务失败"}
        except Exception as e:
            return {"results": f"删除任务失败: {str(e)}"}

class URLToMessageBlock(Block):
    """URL转换Block"""
    name = "url_to_message"
    description = "将结果中的URL转换为IMMessage"
    container: DependencyContainer
    inputs = {
        "text": Input(
            name="text",
            label="含URL的文本",
            data_type=str,
            description="包含URL的文本内容"
        )
    }
    outputs = {
        "message": Output(
            name="message",
            label="消息对象",
            data_type=IMMessage,
            description="转换后的消息对象"
        )
    }

    def __init__(self):
        self.logger = get_logger("URLToMessageBlock")

    def coverAndSendMessage(self, message: str) -> IMMessage:
        # 首先替换掉转义的换行符为实际换行符
        message = message.replace('\\n', '\n')
        # 修改正则表达式以正确处理换行符分隔的URL
        url_pattern = r'https?://[^\s\n<>\"\']+|www\.[^\s\n<>\"\']+'
        # 文件扩展名列表
        image_extensions = {'.jpg', '.jpeg', '.png', '.gif', '.webp', '.bmp', '.ico', '.tiff'}
        audio_extensions = {'.mp3', '.wav', '.ogg', '.m4a', '.aac', '.flac', '.midi', '.mid'}
        video_extensions = {'.mp4', '.avi', '.mov', '.wmv', '.flv', '.mkv', '.webm', '.m4v', '.3gp'}

        try:
            urls = re.findall(url_pattern, message)
            print(urls)
            # If no URLs found, return text message
            if not urls:
                return None
            message_elements = []
            for url in urls:
                try:
                    # Parse URL
                    parsed = urlparse(url)
                    path = unquote(parsed.path)

                    # Get extension from path
                    ext = None
                    if '.' in path:
                        ext = '.' + path.split('.')[-1].lower()
                        if '/' in ext or len(ext) > 10:
                            ext = None

                    # 使用URL直接创建消息对象，而不是下载内容
                    if ext in image_extensions:
                        message_elements.append(ImageMessage(url=url))
                        continue
                    elif ext in audio_extensions:
                        message_elements.append(VoiceMessage(url=url))
                        continue
                    elif ext in video_extensions:
                        message_elements.append(VideoElement(file=url))
                        continue
                    try:
                        response = requests.head(url, allow_redirects=True, timeout=5)
                        content_type = response.headers.get('content-type', '').lower()
                    except Exception as e:
                        self.logger.warning(f"Failed to get headers for {url}: {str(e)}")
                        content_type = ''

                    # Check content type first, then fall back to extension
                    if any(x in content_type for x in ['image', 'png', 'jpg', 'jpeg', 'gif']):
                        message_elements.append(ImageMessage(url=url))
                    elif any(x in content_type for x in ['video', 'mp4', 'avi', 'mov']):
                        message_elements.append(VideoElement(file=url))
                    elif any(x in content_type for x in ['audio', 'voice', 'mp3', 'wav']):
                        message_elements.append(VoiceMessage(url=url))
                except Exception as e:
                    self.logger.error(f"Error processing URL {url}: {str(e)}")
                    continue

            # If we got here, we found URLs but couldn't process them
            if message_elements:
                return IMMessage(
                    sender="bot",
                    raw_message=message,
                    message_elements=message_elements
                )
        except Exception as e:
            self.logger.error(f"Error in coverAndSendMessage: {str(e)}")
        return None

    def execute(self, text: str) -> Dict[str, Any]:
        try:
            # Direct call to coverAndSendMessage
            message = self.coverAndSendMessage(text)
            return {"message": message}
        except Exception as e:
            self.logger.error(f"Error converting URL to message: {str(e)}")
            return {
                "message": IMMessage(
                    sender="bot",
                    raw_message=text,
                    message_elements=[TextMessage("")]
                )
            }

