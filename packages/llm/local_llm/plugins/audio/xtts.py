#!/usr/bin/env python3
import os
import time
import queue
import pprint
import logging

import torch
import torchaudio
import numpy as np

from local_llm import Plugin
from local_llm.utils import download_model, convert_audio

from TTS.tts.configs.xtts_config import XttsConfig
from TTS.tts.models.xtts import Xtts

from .tts import TTSPlugin

class XTTS(TTSPlugin):
    """
    Streaming TTS service using XTTS model with HiFiGAN decoder in TensorRT.
    
    https://huggingface.co/coqui/XTTS-v2
    https://github.com/coqui-ai/TTS

    Inputs:  words to speak (str)
    Output:  audio samples (np.ndarray, int16)
    
    You can get the list of voices with tts.voices, and list of languages with tts.languages
    The speed can be set with tts.rate (1.0 = normal). The default voice is '...' with rate 1.0
    """
    def __init__(self, model='coqui/XTTS-v2', voice='Claribel Dervla', language_code='en', 
                 sample_rate_hz=48000, voice_rate=1.0, use_tensorrt=True, **kwargs):
        """
        Load XTTS model and set default options (many of which can be changed at runtime)
        """
        super().__init__(**kwargs)
        
        if os.path.isdir(model):
            self.model_path = model
            self.model_name = model
        else:
            t = model.lower()
            if XTTS.is_xtts_model(model):
                model = 'coqui/XTTS-v2'
            self.model_path = download_model(model)
            self.model_name = model
            
        self.config_path = os.path.join(self.model_path, 'config.json')
        logging.info(f"loading XTTS model config from {self.config_path}")
        
        self.config = XttsConfig()
        self.config.load_json(self.config_path)
        
        logging.debug(f"XTTS model config for {self.model_name}\n{pprint.pformat(self.config, indent=1)}")
        logging.debug(f"loading XTTS model from {self.model_path}")
        
        self.model = Xtts.init_from_config(self.config)
        
        self.model.load_checkpoint(
            self.config, 
            checkpoint_dir=self.model_path, 
            speaker_file_path=os.path.join(self.model_path, 'speakers_xtts.pth'),
            use_tensorrt=use_tensorrt,
        )
        
        self.model.cuda()

        self.speaker_manager = self.model.speaker_manager
        self.voices = list(self.speaker_manager.speakers.keys())
        self.languages = self.model.language_manager.language_names
        
        logging.info(f"XTTS voices for {self.model_name}:\n{self.voices}")
        logging.info(f"XTTS languages for {self.model_name}:\n{self.languages}")
        
        if voice not in self.voices:
            logging.warning(f"Voice '{voice}' is not a supported voice in {self.model_name}, defaulting to '{self.voices[0]}'")
            voice = self.voices[0]
            
        self.voice = voice
        self.language = language_code
        
        self.rate = voice_rate
        self.sample_rate = sample_rate_hz
        self.model_sample_rate = self.config.model_args.output_sample_rate
        
        if self.sample_rate != self.model_sample_rate:
            self.resampler = torchaudio.transforms.Resample(self.model_sample_rate, self.sample_rate).cuda()
        else:
            self.resampler = None
            
        logging.debug(f"running TTS model warm-up for {self.model_name}")
        self.process("This is a test of the text to speech.")
    
    @property
    def voice(self):
        return self._voice
        
    @voice.setter
    def voice(self, voice):
        if voice not in self.voices:
            raise ValueError(f"'{voice}' was not in the supported list of voices for {self.model_name}\n{self.voices}")
        self._voice = voice
        self.gpt_cond_latent, self.speaker_embedding = self.speaker_manager.speakers[voice].values()
    
    @property
    def language(self):
        return self._language
        
    @language.setter
    def language(self, language):
        language = language.lower().split('-')[0]  # drop the country code (e.g. 'en-US')
        if language not in self.languages:
            raise ValueError(f"'{language}' was not in the supported list of languages for {self.model_name}\n{self.languages}")
        self._language = language
       
    @staticmethod
    def is_xtts_model(model):
        if os.path.isdir(model):
            return 'xtts' in model.lower()
        model_names = ['xtts', 'xtts2', 'xtts-v2', 'xtts_v2', 'coqui/xtts-v2']
        return any([x == model.lower() for x in model_names])
        
    def process(self, text, flush=True, **kwargs):
        """
        Inputs text, outputs stream of audio samples (np.ndarray, np.int16)
        
        The input text is buffered by punctuation/phrases as it sounds better,
        and filtered for emojis/ect, and has SSML tags applied (if enabled) 
        """
        if not flush:
            if len(self.outputs[0]) == 0:
                #logging.debug(f"TTS has no output connections, skipping generation")
                return    
            text = self.buffer_text(text)
            
        text = self.filter_text(text)

        if not text or self.interrupted:
            return
            
        logging.debug(f"generating TTS for '{text}'")
        
        stream = self.model.inference_stream(
            text,
            self.language,
            self.gpt_cond_latent,
            self.speaker_embedding,
            enable_text_splitting=False, #True,
            #overlap_len=128,
            #stream_chunk_size=20,
            do_sample=True,
            speed=self.rate,
        )

        for samples in stream:
            if self.interrupted:
                logging.debug(f"TTS interrupted, terminating request early:  {text}")
                break
                
            if self.resampler:
                samples = self.resampler(samples)
                
            samples = convert_audio(samples, dtype=torch.int16)
            samples = samples.detach().cpu().numpy()

            #logging.debug(f"TTS outputting {len(samples)} audio samples")
            self.output(samples)
            
        #logging.debug(f"done with TTS request '{text}'")