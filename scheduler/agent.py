import json
import re
from typing import Dict, List, Any, Optional

from kirara_ai.llm.format.message import LLMChatMessage,LLMChatTextContent
from kirara_ai.llm.format.request import LLMChatRequest
from kirara_ai.llm.llm_manager import LLMManager
from kirara_ai.llm.llm_registry import LLMAbility
from kirara_ai.ioc.container import DependencyContainer
from kirara_ai.logger import get_logger
from kirara_ai.plugin_manager.plugin_loader import PluginLoader
from kirara_ai.im.adapter import IMAdapter

def execute_tools(
    available_blocks: Dict,
    prompt: List[LLMChatMessage],
    container: DependencyContainer,
    model_name: Optional[str] = None,
    max_retries: int = 3
) -> tuple:
    """Execute tools based on the prompt using LLM to determine actions.

    Args:
        available_blocks: Dictionary of available blocks with names as keys
        prompt: LLM chat messages to analyze
        container: Dependency container for resolving dependencies
        model_name: Name of the model to use, or None to use default
        max_retries: Maximum number of retries for LLM calls

    Returns:
        tuple: (results string, all_results list)
    """
    logger = get_logger("executeTools")
    scheduler = container.resolve(PluginLoader).plugins["scheduler"].scheduler
    scheduler.adapter = container.resolve(IMAdapter)
    llm_manager = container.resolve(LLMManager)

    model_id = model_name or llm_manager.get_llm_id_by_ability(LLMAbility.TextChat)
    if not model_id:
        raise ValueError("No available LLM models found")

    llm = llm_manager.get_llm(model_id)
    if not llm:
        raise ValueError(f"LLM {model_id} not found, please check the model name")

    # Build available actions array
    available_actions = []
    for action_name, block in available_blocks.items():
        action_info = {
            "action": action_name,
            "description": block.description if hasattr(block, 'description') else "",
            "params": {}
        }

        # Get parameter descriptions from block inputs
        for param_name, input_def in block.inputs.items():
            action_info["params"][param_name] = {
                "description": input_def.description,
                "type": str(input_def.data_type.__name__),
                "required": not input_def.nullable if hasattr(input_def, 'nullable') else True
            }

        available_actions.append(action_info)

    system_prompt = f"""你是一个任务解析助手。你需要从用户的对话中理解用户意图并返回相应的操作链（可能包含多个任务）。

可用的操作类型和参数如下：
{json.dumps(available_actions, ensure_ascii=False)}

请按照以下JSON格式返回结果：[{{"action": "<操作名称>","params": {{"<参数名>": "<参数值>"}}}}]

注意：
1. 如果无法理解用户意图或者没有匹配的操作类型，请返回：[]
2. params中只需要包含对应action所需的参数
3. 请直接返回json数组格式数据
"""

    messages = [
        LLMChatMessage(role="system", content=[LLMChatTextContent(text=system_prompt)]),
        *prompt
    ]

    req = LLMChatRequest(messages=messages, model=model_id)

    # Retry logic
    actions = []
    for retry in range(max_retries):
        try:
            response = llm.chat(req).message.content[0].text.replace("\n","")
            logger.debug(response)
            
                # 如果直接解析失败，尝试使用正则表达式匹配 JSON 数组
            json_match = re.search(r'(\[[\s\S]*\])', response)
            if json_match:
                actions = json.loads(json_match.group(1))
                break
            # 如果还是没找到数组，尝试匹配单个 JSON 对象
            json_obj_match = re.search(r'(\{[\s\S]*\})', response)
            if json_obj_match:
                actions = [json.loads(json_obj_match.group(1))]
                break
            raise Exception("无法从响应中解析出有效的 JSON 数据")
        except Exception as e:
            logger.error(f"Retry {retry + 1}/{max_retries}: Error during LLM chat: {str(e)}")
            if retry == max_retries - 1:  # Last retry failed
                return f"执行失败: {str(e)}", []
            continue

    # Execute all parsed actions
    all_results = []
    lastAction = ""
    for i, action in enumerate(actions):
        try:
            # Fill parameters for non-first actions
            if i > 0 and action.get("params", {}) and action.get("action") != lastAction:
                action = fill_params(action, all_results, llm_manager, prompt, available_blocks, container, max_retries, model_name)

            lastAction = action.get("action")
            action_name = action.get("action")
            params = action.get("params", {})

            if action_name not in available_blocks:
                all_results.append(f"未知操作: {action_name}")
                continue

            # Inject container into block
            block_class = available_blocks[action_name]
            block_instance = block_class()
            block_instance.container = container

            # Execute block and collect results
            execution_result = block_instance.execute(**params)

            # Collect all output values
            if isinstance(execution_result, dict):
                result_str = "\n".join([f"{k}:{v}" for k, v in execution_result.items()])
                all_results.append(result_str)
            else:
                all_results.append(str(execution_result).strip())
        except Exception as e:
            all_results.append(f"执行 {action_name} 失败: {str(e)}")

    # Return all results
    final_result = ("你的工具调用运行结果：" + "\n".join(all_results) + "\n") if all_results else "没有工具调用\n"
    return final_result, all_results

def fill_params(
    action: Dict,
    all_results: List[str],
    llm_manager,
    prompt: List[LLMChatMessage],
    available_blocks: Dict,
    container: DependencyContainer,
    max_retries: int = 3,
    model_name: Optional[str] = None
) -> Dict:
    """Use LLM to fill action parameters based on previous execution results

    Args:
        action: Original action dictionary with parameters that might need filling
        all_results: List of previous action execution results
        llm_manager: LLM manager instance
        prompt: Original user prompt messages
        available_blocks: Dictionary of available blocks
        container: Dependency container
        max_retries: Maximum number of retries
        model_name: Model name to use

    Returns:
        Updated action dictionary
    """
    logger = get_logger("fillParams")

    if not all_results:  # If no previous results, return original action
        return action

    action_name = action.get("action", "")
    params = action.get("params", {})

    # Get parameter descriptions for this action type
    param_descriptions = {}
    if action_name in available_blocks:
        block_class = available_blocks[action_name]
        for param_name, input_def in block_class.inputs.items():
            param_descriptions[param_name] = {
                "description": input_def.description,
                "type": str(input_def.data_type.__name__),
                "required": not input_def.nullable if hasattr(input_def, 'nullable') else True
            }

    # Pre-build potentially problematic strings
    json_format_example = '{"param1": "value1", "param2": "value2", ...}'
    previous_results = "\n".join(all_results)
    messages = "\n".join([p.content for p in prompt])

    # Build system prompt
    system_prompt = f"""你是一个参数填充助手。请根据任务要求和之前操作的结果，填充当前操作所需的参数。
任务要求:{messages}
之前操作的结果:{previous_results}
当前操作: {action_name}
参数说明: {json.dumps(param_descriptions, ensure_ascii=False)}
原始参数: {json.dumps(params, ensure_ascii=False)}

请分析之前的结果，并填充当前操作所需的参数。只返回一个JSON对象，包含所有必要的参数，格式如下:
{json_format_example}

注意:
1. 只返回JSON对象，不要添加任何解释或额外文本
2. 保留原始参数中已有的值，除非它们需要根据上下文进行更新
3. 确保参数类型与参数说明中的要求匹配
"""

    model_id = model_name or llm_manager.get_llm_id_by_ability(LLMAbility.TextChat)
    llm = llm_manager.get_llm(model_id)

    messages = [LLMChatMessage(role="user", content=[LLMChatTextContent(text=system_prompt)])]
    req = LLMChatRequest(messages=messages, model=model_id)

    # Retry logic
    for retry in range(max_retries):
        try:
            response = llm.chat(req).message.content[0].text
            json_match = re.search(r'(\{[\s\S]*?\})', response)
            filled_params = json.loads(json_match.group(1))

            if filled_params:  # If parameters successfully parsed
                # Merge original and filled parameters
                updated_action = action.copy()
                updated_action["params"] = {**params, **filled_params}
                return updated_action

            logger.warning(f"Retry {retry + 1}/{max_retries}: Failed to get valid parameters")
        except Exception as e:
            logger.error(f"Retry {retry + 1}/{max_retries}: Error during parameter filling: {str(e)}")
            if retry == max_retries - 1:  # Last retry failed
                break
            continue

    # If all retries fail, return original action
    logger.warning("All parameter filling retries failed, using original parameters")
    return action
