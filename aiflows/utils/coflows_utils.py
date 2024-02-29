from typing import Any
import uuid

import colink as CL
from aiflows.messages import Message,OutputMessage
from aiflows.utils.io_utils import coflows_deserialize, coflows_serialize

PUSH_ARGS_TRANSFER_PATH = "push_tasks"


class FlowFuture:
    def __init__(self, cl, msg_id):
        self.cl = cl
        self.colink_storage_key = f"{PUSH_ARGS_TRANSFER_PATH}:{msg_id}:response"

    def try_get(self):
        """
        Non-blocking read, returns None if there is no response yet.
        """
        return OutputMessage.deserialize(self.cl.read_entry(self.colink_storage_key))

    def get(self, output_data_only=True):
        """
        Blocking read, creates colink queue in background and subscribes to it.
        Probably shouldn't create queue and should have timeout.
        """
        
        message = OutputMessage.deserialize(self.cl.read_or_wait(self.colink_storage_key))
        
        if output_data_only:
            return message.data["output_data"]
        
        return message


def push_to_flow(cl, target_user_id, target_flow_ref, message: Message):
    if target_user_id == "local" or target_user_id == cl.get_user_id():
        participants = [
            CL.Participant(
                user_id=cl.get_user_id(),
                role="receiver",
            ),
        ]
    else:
        participants = [
            CL.Participant(
                user_id=cl.get_user_id(),
                role="initiator",
            ),
            CL.Participant(
                user_id=target_user_id,
                role="receiver",
            ),
        ]

    push_msg_id = uuid.uuid4()
    cl.create_entry(
        f"{PUSH_ARGS_TRANSFER_PATH}:{push_msg_id}:msg",
        message.serialize(),
    )

    push_param = {
        "flow_id": target_flow_ref,
        "message_id": str(push_msg_id),
    }
    cl.run_task("coflows_push", coflows_serialize(push_param), participants, True)
    return push_msg_id


