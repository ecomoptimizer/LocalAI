#!/usr/bin/env python3
import asyncio
from concurrent import futures
import argparse
import signal
import sys
import os

import backend_pb2
import backend_pb2_grpc

import grpc
from vllm.engine.arg_utils import AsyncEngineArgs
from vllm.engine.async_llm_engine import AsyncLLMEngine
from vllm.sampling_params import SamplingParams
from vllm.utils import random_uuid

_ONE_DAY_IN_SECONDS = 60 * 60 * 24

# If MAX_WORKERS are specified in the environment use it, otherwise default to 1
MAX_WORKERS = int(os.environ.get('PYTHON_GRPC_MAX_WORKERS', '1'))

# Implement the BackendServicer class with the service methods
class BackendServicer(backend_pb2_grpc.BackendServicer):
    """
    A gRPC servicer that implements the Backend service defined in backend.proto.
    """
    def generate(self,prompt, max_new_tokens):
        """
        Generates text based on the given prompt and maximum number of new tokens.

        Args:
            prompt (str): The prompt to generate text from.
            max_new_tokens (int): The maximum number of new tokens to generate.

        Returns:
            str: The generated text.
        """
        self.generator.end_beam_search()

        # Tokenizing the input
        ids = self.generator.tokenizer.encode(prompt)

        self.generator.gen_begin_reuse(ids)
        initial_len = self.generator.sequence[0].shape[0]
        has_leading_space = False
        decoded_text = ''
        for i in range(max_new_tokens):
            token = self.generator.gen_single_token()
            if i == 0 and self.generator.tokenizer.tokenizer.IdToPiece(int(token)).startswith('▁'):
                has_leading_space = True

            decoded_text = self.generator.tokenizer.decode(self.generator.sequence[0][initial_len:])
            if has_leading_space:
                decoded_text = ' ' + decoded_text

            if token.item() == self.generator.tokenizer.eos_token_id:
                break
        return decoded_text

    def Health(self, request, context):
        """
        Returns a health check message.

        Args:
            request: The health check request.
            context: The gRPC context.

        Returns:
            backend_pb2.Reply: The health check reply.
        """
        return backend_pb2.Reply(message=bytes("OK", 'utf-8'))

    def LoadModel(self, request, context):
        """
        Loads a language model.

        Args:
            request: The load model request.
            context: The gRPC context.

        Returns:
            backend_pb2.Result: The load model result.
        """
        engine_args = AsyncEngineArgs(
            model=request.Model,
        )

        if request.Quantization != "":
            engine_args.quantization = request.Quantization

        try:
            self.llm = AsyncLLMEngine.from_engine_args(engine_args)
        except Exception as err:
            return backend_pb2.Result(success=False, message=f"Unexpected {err=}, {type(err)=}")
        return backend_pb2.Result(message="Model loaded successfully", success=True)

    async def Predict(self, request, context):
        """
        Generates text based on the given prompt and sampling parameters.

        Args:
            request: The predict request.
            context: The gRPC context.

        Returns:
            backend_pb2.Reply: The predict result.
        """
        gen = self._predict(request, context, streaming=False)
        res = await gen.__anext__()
        return res

    async def PredictStream(self, request, context):
        """
        Generates text based on the given prompt and sampling parameters, and streams the results.

        Args:
            request: The predict stream request.
            context: The gRPC context.

        Returns:
            backend_pb2.Result: The predict stream result.
        """
        iterations = self._predict(request, context, streaming=True)
        try:
            async for iteration in iterations:
                yield iteration
        finally:
            await iterations.aclose()

    async def _predict(self, request, context, streaming=False):

        # Build sampling parameters
        sampling_params = SamplingParams(top_p=0.9, max_tokens=200)
        if request.TopP != 0:
            sampling_params.top_p = request.TopP
        if request.Tokens > 0:
            sampling_params.max_tokens = request.Tokens
        if request.Temperature != 0:
            sampling_params.temperature = request.Temperature
        if request.TopK != 0:
            sampling_params.top_k = request.TopK
        if request.PresencePenalty != 0:
            sampling_params.presence_penalty = request.PresencePenalty
        if request.FrequencyPenalty != 0:
            sampling_params.frequency_penalty = request.FrequencyPenalty
        if request.StopPrompts:
            sampling_params.stop = request.StopPrompts
        if request.IgnoreEOS:
            sampling_params.ignore_eos = request.IgnoreEOS
        if request.Seed != 0:
            sampling_params.seed = request.Seed

        # Generate text
        request_id = random_uuid()
        outputs = self.llm.generate(request.Prompt, sampling_params, request_id)

        # Stream the results
        generated_text = ""
        try:
            async for request_output in outputs:
                iteration_text = request_output.outputs[0].text

                if streaming:
                    # Remove text already sent as vllm concatenates the text from previous yields
                    delta_iteration_text = iteration_text.removeprefix(generated_text)
                    # Send the partial result
                    yield backend_pb2.Reply(message=bytes(delta_iteration_text, encoding='utf-8'))

                # Keep track of text generated
                generated_text = iteration_text
        finally:
            await outputs.aclose()

        # If streaming, we already sent everything
        if streaming:
            return

        # Sending the final generated text
        yield backend_pb2.Reply(message=bytes(generated_text, encoding='utf-8'))

async def serve(address):
    # Start asyncio gRPC server
    server = grpc.aio.server(migration_thread_pool=futures.ThreadPoolExecutor(max_workers=MAX_WORKERS))
    # Add the servicer to the server
    backend_pb2_grpc.add_BackendServicer_to_server(BackendServicer(), server)
    # Bind the server to the address
    server.add_insecure_port(address)

    # Gracefully shutdown the server on SIGTERM or SIGINT
    loop = asyncio.get_event_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(
            sig, lambda: asyncio.ensure_future(server.stop(5))
        )

    # Start the server
    await server.start()
    print("Server started. Listening on: " + address, file=sys.stderr)
    # Wait for the server to be terminated
    await server.wait_for_termination()

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run the gRPC server.")
    parser.add_argument(
        "--addr", default="localhost:50051", help="The address to bind the server to."
    )
    args = parser.parse_args()

    asyncio.run(serve(args.addr))