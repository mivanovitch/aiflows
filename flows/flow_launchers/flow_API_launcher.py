from abc import ABC

import time
from copy import deepcopy

from typing import Any, List, Dict, Union, Optional, Tuple

import hydra
from omegaconf import DictConfig

from flows.flow_launchers import MultiThreadedAPILauncher
from flows.messages import InputMessage
from ..interfaces.abstract import Interface
from ..utils import logging

log = logging.get_logger(__name__)


# single-thread flow launcher
class FlowLauncher(ABC):
    """ A base class for creating a flow launcher.
    """
    @staticmethod
    def launch(flow_with_interfaces: Dict[str, Any],
               data: Union[Dict, List[Dict]],
               path_to_output_file: Optional[str] = None) -> Tuple[List[dict]]:
        """ Static method that takes a flow and runs inference on the given data.
        
        :param flow_with_interfaces: A dictionary containing the flow to run inference with and the input and output interfaces to use.
        :type flow_with_interfaces: Dict[str, Any]
        :param data: The data to run inference on.
        :type data: Union[Dict, List[Dict]]
        :param path_to_output_file: A path to a file to write the outputs to.
        :type path_to_output_file: Optional[str], optional
        :return: A tuple containing the full outputs and the human-readable outputs.
        :rtype: Tuple[List[dict]]
        """
        flow = flow_with_interfaces["flow"]
        input_interface = flow_with_interfaces.get("input_interface", None)
        output_interface = flow_with_interfaces.get("output_interface", None)
        if isinstance(data, dict):
            data = [data]

        full_outputs = []
        human_readable_outputs = []
        for sample in data:
            sample = deepcopy(sample)
            flow.reset(full_reset=True, recursive=True)  # Reset the flow to its initial state

            if input_interface is not None:
                input_data_dict = input_interface(goal="[Input] Run Flow from the Launcher.",
                                                  data_dict=sample,
                                                  src_flow=None,
                                                  dst_flow=flow)
            else:
                input_data_dict = sample

            input_message = InputMessage.build(
                data_dict=input_data_dict,
                src_flow="Launcher",
                dst_flow=flow.name
            )

            output_message = flow(input_message)
            output_data = output_message.data["output_data"]
            
            if output_interface is not None:
                output_data = output_interface(goal="[Output] Run Flow from the Launcher.",
                                               data_dict=output_data,
                                               src_flow=flow,
                                               dst_flow=None)
            human_readable_outputs.append(output_data)

            if path_to_output_file is not None:
                output = {
                    "id": sample["id"],
                    "inference_outputs": [output_message],
                    "error": None
                }
                full_outputs.append(output)

        if path_to_output_file is not None:
            FlowMultiThreadedAPILauncher.write_batch_output(full_outputs,
                                                            path_to_output_file=path_to_output_file,
                                                            keys_to_write=["id",
                                                                           "inference_outputs",
                                                                           "error"])

        return full_outputs, human_readable_outputs


class FlowMultiThreadedAPILauncher(MultiThreadedAPILauncher):
    """
    A class for querying the APIs using the litellm library with interactive chatting capabilities.

    :param flows: The flow (or a list of independent instances of the same flow) to run the inference with.
    :type flows: List[Dict[str, Any]]
    :param n_independent_samples: the number of times to independently repeat the same inference for a given sample
    :type n_independent_samples: int
    :param fault_tolerant_mode: whether to crash if an error occurs during the inference for a given sample
    :type fault_tolerant_mode: bool
    :param n_batch_retries: the number of times to retry the batch if an error occurs
    :type n_batch_retries: int
    :param wait_time_between_retries: the number of seconds to wait before retrying the batch
    :type wait_time_between_retries: int
    """

    flows: List[Dict[str, Any]] = None

    def __init__(
            self,
            n_independent_samples: int,
            fault_tolerant_mode: bool,
            n_batch_retries: int,
            wait_time_between_retries: int,
            output_keys: List[str],
            **kwargs,
    ):
        
        super().__init__(**kwargs)
        self.n_independent_samples = n_independent_samples
        self.fault_tolerant_mode = fault_tolerant_mode
        self.n_batch_retries = n_batch_retries
        self.wait_time_between_retries = wait_time_between_retries
        self.output_keys = output_keys
        assert self.n_independent_samples > 0, "The number of independent samples must be greater than 0."

    def predict(self, batch: List[dict]) -> List[dict]:
        """ Runs inference for the given batch.
        
        :param batch: The batch to run inference for.
        :type batch: List[dict]
        :return: The batch with the inference outputs added to it.
        :rtype: List[dict]
        """
        assert len(batch) == 1, "The Flow API model does not support batch sizes greater than 1."
        _resource_id = self._resource_IDs.get()  # The ID of the resources to be used by the thread for this sample
        flow_with_interfaces = self.flows[_resource_id]
        flow = flow_with_interfaces["flow"]
        input_interface = flow_with_interfaces["input_interface"]
        output_interface = flow_with_interfaces["output_interface"]
        path_to_output_file = self.paths_to_output_files[_resource_id]

        for sample in batch:
            if input_interface is not None:
                input_data_dict = input_interface(
                    goal="[Input] Run Flow from the Launcher.",
                    data_dict=sample,
                    src_flow=None,
                    dst_flow=flow
                )
            else:
                input_data_dict = sample

            inference_outputs = []
            human_readable_outputs = []
            _error = None
            for _sample_idx in range(self.n_independent_samples):
                log.info("Running inference for ID (sample {}): {}".format(_sample_idx, sample["id"]))
                _error = None

                if self.fault_tolerant_mode:
                    _attempt_idx = 1

                    while _attempt_idx <= self.n_batch_retries:
                        try:
                            input_message = InputMessage.build(
                                data_dict=input_data_dict,
                                src_flow="Launcher",
                                dst_flow=flow.name
                            )

                            output_message = flow(input_message)
                            output_data = output_message.data["output_data"]
                            if output_interface is not None:
                                output_data = output_interface(
                                    goal="[Output] Run Flow from the Launcher.",
                                    data_dict=output_data,
                                    src_flow=flow,
                                    dst_flow=None
                                )

                            inference_outputs.append(output_message.data)
                            human_readable_outputs.append(output_data)
                            _success_sample = True
                            _error = None
                            break
                        except Exception as e:
                            log.error(
                                f"[Problem `{sample['id']}`] "
                                f"Error {_attempt_idx} in running the flow: {e}. "
                                f"Retrying in {self.wait_time_between_retries} seconds..."
                            )
                            _attempt_idx += 1
                            time.sleep(self.wait_time_between_retries)

                            _error = str(e)

                else:
                    # For development and debugging purposes
                    input_message = InputMessage.build(
                        data_dict=input_data_dict,
                        src_flow="Launcher",
                        dst_flow=flow.name,
                    )
                    output_message = flow(input_message)
                    output_data = output_message.data["output_data"]
                    if output_interface is not None:
                        output_data = output_interface(
                            goal="[Output] Run Flow from the Launcher.",
                            data_dict=output_data,
                            src_flow=flow,
                            dst_flow=None
                        )

                    inference_outputs.append(output_message)
                    human_readable_outputs.append(output_data)
                    _error = None

                if _error is not None:
                    # Break if one of the independent samples failed
                    break
                flow.reset(full_reset=True, recursive=True)  # Reset the flow to its initial state

            sample["inference_outputs"] = inference_outputs  # ToDo: Use an output object instead of the sample directly
            sample["human_readable_outputs"] = human_readable_outputs
            # ToDo: how is None written/loaded to/from a JSON file --> Mention this in the documentation and remove
            sample["error"] = _error

        self.write_batch_output(batch,
                                path_to_output_file=path_to_output_file,
                                keys_to_write=["id",
                                               "inference_outputs",
                                               "human_readable_outputs",
                                               "error"])

        self._resource_IDs.put(_resource_id)
        return batch
