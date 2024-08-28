# SPDX-FileCopyrightText: 2024 Tim Chinowsky
# SPDX-License-Identifier: MIT

import array
import board
import rp2pio

import adafruit_pioasm

class PCM():
    def __init__(
        self, 
        channels=2,
        sample_rate=48000,
        width=16,
        clk_pin=board.D5, # SYNC will be one higher, e.g. D6
        out_pin=board.D9,
        in_pin=board.D10
    ):
        self.channels = channels
        self.width = width
        self.clk_pin = clk_pin
        self.out_pin = out_pin
        self.in_pin = in_pin
        self.clock_multiplier = 12
        codec_clock = sample_rate * channels * width
        pio_clock = self.clock_multiplier * codec_clock
        self.pio_code = f"""
            .program codec
            .side_set 2
                set x {channels-1} side 0b00
                out pins 1 side 0b11 [1]
                set y {width-2} side 0b11 [3]
                in pins 1 side 0b10
                jmp out side 0b10 [4]
            bit_loop:
                mov x x side 0b00 [2]
            word_loop:
                mov x x side 0b00 
            out:
                out pins 1 side 0b01 [2]
                mov x x side 0b01 [2]
                in pins 1 side 0b00  
                jmp y-- bit_loop side 0b00
                set y {width-1} side 0b00 
                pull noblock side 0b00 
                push noblock side 0b00
                jmp x-- word_loop side 0b00
        """
        self.pio_params = {
            "frequency": pio_clock,
            "first_out_pin": out_pin,
            "first_in_pin": in_pin,
            "first_sideset_pin": clk_pin,
            "sideset_pin_count": 2,
            "auto_pull": False,
            "auto_push": False,
            "out_shift_right": False,
            "in_shift_right": False,
            "pull_threshold": 32,
            "wait_for_txstall": False,
            "wrap_target": 0,
        }
        self.pio_instructions = adafruit_pioasm.assemble(self.pio_code)
        self.pio = rp2pio.StateMachine(self.pio_instructions, **self.pio_params)
    
    def __del__(self):
        self.pio.deinit()

    def status(self):
        print(f"actual sample frequency {self.pio.frequency/self.clock_multiplier/self.channels/self.width:9.1f} Hz")
        print(f"               bit clock {self.pio.frequency/self.clock_multiplier:9.1f} Hz")
        print(f"               pio clock {self.pio.frequency:9.1f} Hz\n")

    def test(self, length=None):
        if length is None:
            length = self.channels * 16
        u32 = array.array("L", [0]   * length)
        for i in range(length):
            u32[i] = i << (32-self.bits)
        while True:
            self.pio.write(u32)