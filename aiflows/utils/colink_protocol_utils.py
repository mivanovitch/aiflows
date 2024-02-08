import hydra
from aiflows.base_flows import Flow
import colink as CL
import pickle
from typing import List, Dict, Callable
from aiflows.utils.colink_helpers import get_next_update_message


def get_simple_invoke_protocol(name, ephemeral_flow_create: Callable = None):
    
    use_ephemeral_flow = ephemeral_flow_create is not None
    
    pop = CL.ProtocolOperator(name)
    @pop.handle("simple-invoke:initiator")
    def run_simpleinvoke_initiator(
        cl: CL.CoLink, param: bytes, participants: List[CL.Participant]
    ):
        print("\n~~~ simple-invoke:initiator task_id =", cl.get_task_id(), "~~~")
        if len(participants) > 2:
            print(
                "Warning: simple-invoke protocol expects two participants: initiator and target. Current task has",
                len(participants),
                "participants",
            )
        target_flow_queue = pickle.loads(param)
        print("Target flow queue:", target_flow_queue)
        # ~~~ Get input from task starter ~~~
        print("Received input from task starter...")
        input_msg_b = cl.read_or_wait(
            f"simple-invoke-init:{target_flow_queue}:{cl.get_task_id()}:input_msg",
        )

        # ~~~ Invoke target-flow protocol operator ~~~
        print("Invoking target-flow worker...")
        cl.send_variable("input_msg", input_msg_b, [participants[1]])
        output_msg_b = cl.recv_variable("output_msg", participants[1])

        # ~~~ Return output to task starter ~~~
        print("Relaying output to task starter...")
        cl.create_entry(
            f"simple-invoke-init:{target_flow_queue}:{cl.get_task_id()}:output_msg",
            output_msg_b,
        )


    @pop.handle("simple-invoke:target-flow")
    def run_simpleinvoke_targetflow(
        cl: CL.CoLink, param: bytes, participants: List[CL.Participant]
    ):
        print("\n~~~ simple-invoke:target-flow task_id =", cl.get_task_id(), "~~~")

        target_flow_queue = pickle.loads(param)
        print("Target flow queue:", target_flow_queue)
        print("participants are", participants)
        
        if not use_ephemeral_flow:
            # ~~~ Setup response queue ~~~
            response_queue_name = f"simple-invoke-serve:{target_flow_queue}:{cl.get_task_id()}"
            response_queue = cl.subscribe(
                response_queue_name,
                None,
            )
            response_subscriber = cl.new_subscriber(response_queue)

        # ~~~ Get input from initiator ~~~
        print("Receiving input from initiator worker...")
        input_msg_b = cl.recv_variable("input_msg", participants[0])
        
        input_msg = pickle.loads(input_msg_b)
        print("Input data",input_msg.data)
        
        if not use_ephemeral_flow:
            # ~~~ Invoke target flow ~~~
            print("Pushing to", target_flow_queue)
            input_msg.data["colink_meta_data"]["response_queue_name"] = response_queue_name
            cl.update_entry(target_flow_queue, pickle.dumps(input_msg))

            # ~~~ Read output ~~~
            output_msg = get_next_update_message(response_subscriber)
            
        else:        
            flow = ephemeral_flow_create()
            
            output_msg = flow(input_msg)
            if "meta_data" not in output_msg.data:
                output_msg.data["colink_meta_data"] = {}
            
            output_msg.data["colink_meta_data"]["state"] = flow.__getstate__(ignore_colink_info=True)

        # ~~~ Return output to parent ~~~
        print("Relaying output to initiator...")
        cl.send_variable("output_msg", pickle.dumps(output_msg), [participants[0]])
    
    return pop

