#!/usr/bin/env python 3.12
# -*- coding: utf-8 -*-
# time: 2026/07/24
# name: Haowen Cui

import json
import time
from pathlib import Path

from langchain_openai import ChatOpenAI
from pydantic import BaseModel, Field
from langchain_core.prompts import ChatPromptTemplate

BASE_DIR = Path(__file__).resolve().parent.parent
CONFIG_PATH = BASE_DIR / "config.json"
SUMMARY_DIR = BASE_DIR / "data" / "summaries"

PROMPT_TEMPLATE = ChatPromptTemplate.from_messages(
        [
            (
                "system",
                """
                你是一名视频内容总结助手。
                
                请根据用户提供的视频文字稿，生成准确、清晰的中文总结。
                
                要求：
                1. summary 使用一段话概括视频主要内容。
                2. key_points 包含 3 到 5 个关键内容。
                3. keywords 包含 5 到 10 个关键词。
                4. 不要添加文字稿中没有提到的信息。
                5. 字幕可能存在识别错误，请结合上下文理解。
                6. 不要因为字幕识别错误而编造人物、平台或事件名称。
                """,
            ),
            ("human",
                """
                {retry_instruction}
                
                视频文字稿：
                
                {transcript}
                """,),
        ]
    )



class VideoSummary(BaseModel):
    summary: str = Field(
        description="用一段中文概括视频的主要内容"
    )

    key_points: list[str] = Field(
        description="3到5个关键内容"
    )

    keywords: list[str] = Field(
        description="5到10个关键词"
    )

def load_config():
    """
    读取模型配置。

    :return: 模型配置信息
    """
    with open(CONFIG_PATH,"r",encoding="utf-8", ) as file:
        config = json.load(file)

    return config


def create_model():
    """
    创建百炼 Qwen 模型。

    :return: LangChain 模型
    """
    config = load_config()

    api_key = config.get("api_key")
    model_name = config.get("model")
    base_url = config.get("base_url")

    if not api_key:
        raise ValueError("config.json 中没有 api_key")

    if not model_name:
        raise ValueError("config.json 中没有 model")

    if not base_url:
        raise ValueError("config.json 中没有 base_url")

    model = ChatOpenAI(
        api_key=api_key,
        model=model_name,
        base_url=base_url,
        temperature=0.2,
    )

    return model


def load_transcript(pathname):
    """
    读取 transcript JSON。

    :param pathname: transcript JSON 文件路径
    :return: 文字稿数据
    """
    with open(pathname,"r",encoding="utf-8",) as file:
        transcript = json.load(file)

    if not transcript.get("video_id"):
        raise ValueError("文字稿中没有 video_id")

    if not transcript.get("text"):
        raise ValueError("文字稿中没有 text")

    return transcript

def get_raw_response_text(raw_message):
    """
    从模型原始回复中提取可读内容。

    :param raw_message: LangChain 原始模型回复
    :return: 原始回复内容
    """
    if raw_message is None:
        return ""

    if raw_message.content:
        return str(raw_message.content)

    return str(raw_message.additional_kwargs)

def summarize_video(transcript_text):
    """
    根据视频文字稿生成结构化总结。

    第一次生成失败后，最多重新生成两次。

    :param transcript_text: 完整视频文字稿
    :return: 视频总结字典
    """
    model = create_model()

    structured_model = (
        model.with_structured_output(
            VideoSummary,
            include_raw=True,
        )
    )

    chain = (PROMPT_TEMPLATE| structured_model)

    retry_instruction = ""
    last_error = None

    for attempt in range(3):
        try:
            result = chain.invoke(
                {
                    "transcript": transcript_text,
                    "retry_instruction": (retry_instruction),
                }
            )

            summary = result.get("parsed")
            parsing_error = result.get("parsing_error")

            if summary is not None:
                return summary.model_dump()

            last_error = parsing_error

            raw_text = get_raw_response_text(result.get("raw"))

            retry_instruction = f"""
            上一次输出没有通过结构验证，请重新生成。
            
            错误信息：
            {parsing_error}
            
            上一次模型输出：
            {raw_text}
            
            请严格按照规定的字段和数量重新生成。
            不要解释错误，只返回符合结构的结果。
            """

        except Exception as error:
            last_error = error

            retry_instruction = f"""
            上一次模型调用或结构化解析失败。
            
            错误信息：
            {error}
            
            请重新阅读视频文字稿，并严格按照规定的结构生成结果。
            """

        if attempt < 2:
            print(
                f"结构化总结失败，"
                f"正在进行第 {attempt + 1} 次重试"
            )

    raise RuntimeError(
        "首次生成及两次重试均失败："
        f"{last_error}"
    )


def save_summary(summary, video_id):
    """
    保存视频总结。

    :param summary: 视频总结字典
    :param video_id: Bilibili 视频 ID
    :return: 总结文件路径
    """
    SUMMARY_DIR.mkdir(
        parents=True,
        exist_ok=True,
    )

    summary["video_id"] = video_id

    output_path = SUMMARY_DIR / f"{video_id}.json"

    output_path.write_text(json.dumps(summary,ensure_ascii=False,indent=2,),encoding="utf-8",)

    return output_path

def _test():
    """
    测试百炼 Qwen 是否可以正常调用。

    :return: None
    """
    start_time = time.perf_counter()

    model = create_model()

    response = model.invoke("请只回复：Qwen 模型连接成功" )

    end_time = time.perf_counter()

    print("-" * 40)
    print(response.content)
    print(
        f"模型调用耗时："
        f"{end_time - start_time:.2f} 秒"
    )
    print("-" * 40)



if __name__ == "__main__":
    try:
        start_time=time.perf_counter()

        transcript_path=(
                BASE_DIR
                / "data"
                / "transcripts"
                / "BV1Ru6BBwEAn.json"
        )

        transcript=load_transcript(transcript_path)

        summary=summarize_video(transcript["text"])

        summary_path=save_summary(summary,transcript["video_id"],)

        end_time=time.perf_counter()

        print("-" * 40)
        print(json.dumps(summary,ensure_ascii=False,indent=2,))
        print("-" * 40)
        print(f"总结保存路径：{summary_path}")
        print(f"视频总结总耗时：{end_time - start_time:.2f} 秒")

    except Exception as error:
        print(f"视频总结失败：{error}")