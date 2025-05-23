from typing import Any, Dict, List, Optional,Annotated
import asyncio
from kirara_ai.workflow.core.block import Block, Input, Output, ParamMeta
from kirara_ai.llm.format.message import LLMChatMessage
from kirara_ai.ioc.container import DependencyContainer
from kirara_ai.im.adapter import IMAdapter
from kirara_ai.im.manager import IMManager
from kirara_ai.im.message import IMMessage, MessageElement, TextMessage, ImageMessage, VoiceMessage, MediaMessage,VideoElement
from kirara_ai.im.sender import ChatSender
from kirara_ai.llm.llm_manager import LLMManager
from kirara_ai.llm.model_types import LLMAbility, ModelType
import re
from kirara_ai.logger import get_logger
from kirara_ai.workflow.core.block.registry import BlockRegistry
import requests
from kirara_ai.plugin_manager.plugin_loader import PluginLoader
from urllib.parse import urlparse, unquote
from .agent import execute_tools

def im_adapter_options_provider(container: DependencyContainer, block: Block) -> List[str]:
    return [key for key, _ in container.resolve(IMManager).adapters.items()]

class CreateTaskBlock(Block):
    """定时任务Block"""
    name = "create_task"
    description = "创建定时任务"
    container: DependencyContainer
    inputs = {
        "cron": Input(name="定时任务的标准的5字段cron表达式",label="cron", data_type= str, description="定时任务的标准的5字段cron表达式"),
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
            if isinstance(target, str):
                    target = None
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
    return llm_manager.get_supported_models(ModelType.LLM, LLMAbility.TextChat)

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
            # 大模型会传错参导致发送错误
            if isinstance(target, str):
                target = None
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
        max_retries: Annotated[
            Optional[str],
            ParamMeta(label="重试次数", description="提取json的重试次数"),
        ] = 3,
        available_block_names: Annotated[
            Optional[str],
            ParamMeta(label="可用插件名字", description="本block可用插件名字，逗号分隔"),
        ] = "create_task,create_one_time_task,get_tasks,delete_task,delete_all_tasks,image_generate,web_search,music_search",
    ):
        self.model_name = model_name
        self.max_retries = max_retries
        self.logger = get_logger("findSchedulerCron")
        self.scheduler = None

        # 获取所有可用的block名称列表
        self.block_names = [name.strip() for name in available_block_names.split(",")]

        # 初始化可用的blocks字典
        self.available_blocks = {}

    def execute(self, prompt: List[LLMChatMessage]) -> Dict[str, Any]:
        # Initialize scheduler and get available blocks
        self.scheduler = self.container.resolve(PluginLoader).plugins["scheduler"].scheduler
        self.scheduler.adapter = self.container.resolve(IMAdapter)
        self.scheduler.container = self.container
        registry: BlockRegistry = self.container.resolve(BlockRegistry)

        for block in registry.get_all_types():
            if block.name in self.block_names:
                self.available_blocks[block.name] = block
        last_contents = prompt[-1].content
        this_content = ""
        for last_content in last_contents:
            if last_content.type == "text":
                this_content += last_content.text
        self.logger.debug(this_content)
        if "触发定时任务" in this_content:
            if "create_one_time_task" in self.available_blocks:
                self.available_blocks.pop("create_one_time_task")
            if "create_task" in self.available_blocks:
                self.available_blocks.pop("create_task")
        # Use the extracted method from agent.py
        results, all_results = execute_tools(
            available_blocks=self.available_blocks,
            prompt=prompt,
            container=self.container,
            model_name=self.model_name,
            max_retries=self.max_retries
        )
        return {"results": results}

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

            if isinstance(target, str):
                target = None
            chat_id = str(target) if target  else  str(self.container.resolve(IMMessage).sender)
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
        if isinstance(target, str):
            target = None
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
                    if any(x in url for x in image_extensions):
                        message_elements.append(ImageMessage(url=url))
                        continue
                    elif any(x in url for x in audio_extensions):
                        message_elements.append(VoiceMessage(url=url))
                        continue
                    elif any(x in url for x in video_extensions):
                        message_elements.append(VideoElement(url=url))
                        continue
                    try:
                        response = requests.head(url, allow_redirects=True, timeout=5)
                        content_type = response.headers.get('Content-Type', '').lower()
                    except Exception as e:
                        self.logger.warning(f"Failed to get headers for {url}: {str(e)}")
                        content_type = ''
                    self.logger.debug(content_type)
                    # Check content type first, then fall back to extension
                    if any(x in content_type for x in ['image', 'png', 'jpg', 'jpeg', 'gif']):
                        message_elements.append(ImageMessage(url=url))
                    elif any(x in content_type for x in ['video', 'mp4', 'avi', 'mov']):
                        message_elements.append(VideoElement(url=url))
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

