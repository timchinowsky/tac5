# SPDX-FileCopyrightText: 2024 Tim Chinowsky
# SPDX-License-Identifier: MIT

import array
import audiocore
import board
import math
import os
import sdcardio
import storage
import time
import usb_cdc

import pcm

def reverse(obj):
    for i in range(len(obj)-1, -1, -1):
        yield obj[i]

def reversebits2int(value, width):
    mask = (1 << width) - 1
    value = value & mask
    bits = f'{value:0{width}b}'
    print(bits)
    reversed_bits = ''.join(list(reverse(bits)))
    print(reversed_bits)
    return int(reversed_bits, 2)

def int2bits(value, width):
    if value < 0:
        value = (1 << width) + value
    mask = (1 << width) - 1
    return value & mask

def bits2int(value, width):
    mask = (1 << width) - 1
    value = value & mask
    if value & (1 << (width - 1)):
        value -= (1 << width)
    return value

def octave_wave(length=100, channels=2, sample_width=None, pad_after=25, amplitude=0.7):
    wave = array.array('L', [0] * (length * channels + pad_after * channels))
    wave_width = 32
    if sample_width is None:
        sample_width = wave_width
    for c in range(channels):
        for i in range(length):
            f = c + 1
            a = 2**(sample_width-1) - 1
            val = int(a * amplitude * math.sin(i/length * 2 * math.pi * f))
            #val = f
            # val = (c+(c<<4)+(c<<8)+(c<<12)) & ((1 << sample_width) - 1)
            # wave[i*channels+c] = val << (wave_width-sample_width)
            wave[i*channels+c] = int2bits(val, sample_width) << (wave_width-sample_width)
    return wave


class TAC5():
    def __init__(self, 
                 address=None, 
                 i2c=None, 
                 clk_pin=board.D5, # sync will be one higher, e.g. D6
                 out_pin=board.D9, 
                 in_pin=board.D10, 
                 width=16,
                 sample_rate=48000
                ):
        if i2c is None:
            self.i2c = board.I2C()
        else:
            self.i2c = i2c
        if address is None:
            self.address = [a for a in self.scan() if a>=0x50 and a<=0x53]
        elif type(address)==int:
            self.address = [address]
        else:
            self.address = address
        self.codecs = len(self.address)
        self.channels = 2 * self.codecs
        self.clk_pin = clk_pin
        self.out_pin = out_pin
        self.in_pin = in_pin
        self.slots = {}
        self.width = width
        self.sample_rate=sample_rate
        self.pcm = None
        self.play_buffer = None
        self.record_buffer = None
    
    def __del__(self):
        del self.pcm

    def configure(self, address='all'):

        if self.pcm is not None:
            del self.pcm

        for i, a in enumerate(self.address_list(address)):
            # reset and enable device
            self.write_reg(0x01, 1, address=a)         # Reset all registers to defaults
            time.sleep(0.1)
            self.write_reg(0x02, 9, address=a)         # No sleep, DREG and VREF enabled
            if self.width == 32:
                self.write_reg(0x1A, 0x30, address=a)  # TDM, 32 bit
            elif self.width ==24:
                self.write_reg(0x1A, 0x20, address=a)  # TDM, 24 bit
            elif self.width == 20:
                self.write_reg(0x1A, 0x10, address=a)  # TDM, 20 bit
            else:                
                self.write_reg(0x1A, 0x00, address=a)  # TDM, 16 bit
            self.write_reg(0x78, 0xEE, address=a)      # Power up all enabled ADC and DAC channels
            # self.write_reg(0x72, 0x0A, address=a)      # disable ADC HPF
            self.write_reg(0x72, 0x8A, address=a)      # disable ADC HPF, ultra-low latency decimation filter
            self.write_reg(0x73, 0x0A, address=a)      # disable DAC HPF
            self.write_reg(0x1B, 0x40, address=a)      # transmit hi-Z for unused cycles
            
            self.write_reg(0x50, 0x4A, address=a)
            self.write_reg(0x55, 0x4A, address=a)
            
            #self.write_reg(0x50, 0x2B, address=a)      # ADC1 diff, 40k, rail-to-rail, 2 Vrms SE, 96kHz
            #self.write_reg(0x55, 0x2B, address=a)      # ADC2 diff, 40k, rail-to-rail, 2 Vrms SE, 96kHz
            # self.write_reg(0x64, 0x28, address=a)      # DAC1 SE
            # self.write_reg(0x6B, 0x28, address=a)      # DAC2 SE

            # each slot is a tuple (source, location)
            slots = ((1, i*2), (1, i*2+1), (0, 0), (0, 0), (0, 0), (0, 0), (0, 0), (0, 0))
            self.slots[a] = slots

            # RX slots
            for j in range(len(slots)):
                reg = 0x28+j
                value = slots[j][0]<<5 | slots[j][1]
                self.write_reg(reg, value, address=a)

            # TX slots
            for j in range(len(slots)):
                reg = 0x1E+j
                value = slots[j][0]<<5 | slots[j][1]
                self.write_reg(reg, value, address=a)

        self.pcm = pcm.PCM(channels=self.channels,
                        sample_rate=self.sample_rate,
                        width=self.width, 
                        clk_pin=self.clk_pin,
                        out_pin=self.out_pin,
                        in_pin=self.in_pin)

    def play(self, buffer=None, loop=True, reset=False, length=None):
        if reset or self.pcm is None:
            self.configure()
        if buffer is None:
            if length is None:
                buffer = octave_wave(channels=self.channels, sample_width=self.width)
            else:
                buffer = octave_wave(channels=self.channels, sample_width=self.width, length=length)
            self.play_buffer = buffer
        print('playing...')
        self.pcm.pio.stop()
        self.pcm.pio.background_write(loop=buffer)
        self.pcm.pio.restart()

    def rec(self, buffer=None, reset=False, length=None):
        if reset or self.pcm is None:
            self.configure()
        if (buffer is None and self.record_buffer is None) or length is not None:
            if self.play_buffer is not None and length is None:
                buffer = array.array('L', [0] * len(self.play_buffer))
            else:
                if length is None:
                    buffer = octave_wave(channels=self.channels, sample_width=self.width)
                else:
                    buffer = octave_wave(channels=self.channels, sample_width=self.width, length=length)
                buffer = array.array('L', [0] * len(buffer))
            self.record_buffer = buffer
        elif buffer is None:
            buffer = self.record_buffer
        else:
            self.record_buffer = buffer
        print('recording...')           
        self.pcm.pio.background_read(loop=buffer)

    def show(self, buffer):
        print('sample', end=';')
        for j in range(self.channels-1):
            print(f'ch{j}', end=';')
        print(f'ch{self.channels-1}')
        for i in range(len(buffer)//self.channels):
            print(i,end=';')
            for j in range(self.channels-1):
                val = buffer[j+i*self.channels] << (32-self.width)
                print(bits2int(val>>(32-self.width), self.width), end=';')
            val = buffer[(self.channels-1)+i*self.channels] << (32-self.width)
            print(bits2int(val>>(32-self.width), self.width))

    def record(self, buffer=None, reset=False, length=None):
        if reset or self.pcm is None:
            self.configure()
        if (buffer is None and self.record_buffer is None) or length is not None:
            if self.play_buffer is not None and length is None:
                buffer = array.array('L', [0] * len(self.play_buffer))
            else:
                if length is None:
                    buffer = octave_wave(channels=self.channels, sample_width=self.width)
                else:
                    buffer = octave_wave(channels=self.channels, sample_width=self.width, length=length)
                buffer = array.array('L', [0] * len(buffer))
            self.record_buffer = buffer
        elif buffer is None:
            buffer = self.record_buffer
        else:
            self.record_buffer = buffer
        self.pcm.pio.readinto(buffer)
        print('sample', end=';')
        for j in range(self.channels-1):
            print(f'record{j}', end=';')
        print(f'record{self.channels-1}')
        for i in range(len(buffer)//self.channels):
            print(i,end=';')
            for j in range(self.channels-1):
                val = buffer[j+i*self.channels] << (32-self.width)
                print(bits2int(val>>(32-self.width), self.width), end=';')
            val = buffer[(self.channels-1)+i*self.channels] << (32-self.width)
            print(bits2int(val>>(32-self.width), self.width))

    def playrecord(self, play_buffer=None, record_buffer=None, loop=False, reset=False):
        if reset or self.pcm is None:
            self.configure()
        if play_buffer is None:
            play_buffer = octave_wave(channels=self.channels, sample_width=self.width)
        if record_buffer is None:
            record_buffer = array.array('L', [0] * len(play_buffer))
        self.play_buffer = play_buffer
        self.record_buffer = record_buffer
        self.pcm.pio.restart()
        self.pcm.pio.write_readinto(play_buffer, record_buffer)
        while loop:
            self.pcm.pio.write_readinto(play_buffer, record_buffer)
        print('sample', end=',')
        for j in range(self.channels):
            print(f'play{j}', end=',')
        for j in range(self.channels):
            print(f'record{j}', end=',')
        print()
        for i in range(len(play_buffer)//self.channels):
            print(i,end=',')
            for j in range(self.channels):
                val = play_buffer[j+i*self.channels]
                print(bits2int(val>>(32-self.width), self.width), end=',')
            for j in range(self.channels):
                val = record_buffer[j+i*self.channels] << (32-self.width)
                print(bits2int(val>>(32-self.width), self.width), end=',')
            print()

    def address_list(self, address='all'):
        if address=='all':
            return self.address
        elif type(address)==int:
            if address in self.address:
                return [address]
            else:
                ValueError('Address', address, 'not present')
        else:
            try:
                for a in address:
                    assert(a in self.address)
                return address
            except:
                ValueError('Address', address, 'not present')
    
    def write_reg(self, reg, data, page=0, address='all'):
        while not self.i2c.try_lock():
            pass
        for a in self.address_list(address):
            buf = bytearray(2)
            buf[0] = 0
            buf[1] = page
            self.i2c.writeto(a, buf)
            buf[0] = reg
            buf[1] = data
            self.i2c.writeto(a, buf)
            # print(f'{page}/0x{reg:02.2x}@{a:02.2x} <- 0x{data:02.2x}')
        self.i2c.unlock()

    def read_reg(self, reg, page=0, address='all'):
        while not self.i2c.try_lock():
            pass
        read_data = []
        for a in self.address_list(address):
            buf = bytearray(2)
            buf[0] = 0
            buf[1] = page
            self.i2c.writeto(a, buf)
            buf[0] = reg
            register = bytearray(1)
            register[0] = reg
            data = bytearray(1)
            self.i2c.writeto(a, register)
            self.i2c.readfrom_into(a, data)
            read_data.append(data[0])
        self.i2c.unlock()
        return read_data
    
    def dash(self):
        stay = True
        while stay:
            t = read_serial(usb_cdc.console)
            if len(t)>0:
                print(t)
        
    def read_all(self, page=0, address='all'):
        while not self.i2c.try_lock():
            pass
        print()
        all_regs = []
        for a in self.address_list(address):
            address_regs=[]
            print(f'TAC5 x{a:02.2x} page {page}:')
            print("   x0 x1 x2 x3 x4 x5 x6 x7 x8 x9 xA xB xC xD xE xF")
            contents = bytearray(1)
            register = bytearray(1)
            set_page = bytearray(2)
            set_page[0] = 0
            set_page[1] = page
            self.i2c.writeto(a, set_page)
            for j in range(0, 8):
                print(f'{j:1x}x ', end='')
                for i in range(0, 16):
                    register[0] = i+16*j
                    self.i2c.writeto(a, register)
                    self.i2c.readfrom_into(a, contents)
                    print(f'{contents[0]:02.2x} ', end='')
                    address_regs.append(contents[0])
                print()
            print()
            all_regs.append(address_regs)
        self.i2c.unlock()
        return all_regs

    def scan(self):
        while not self.i2c.try_lock():
            pass
        found = self.i2c.scan()
        self.i2c.unlock()
        return found

def wav():
    sd = sdcardio.SDCard(board.SPI(), board.D25)
    vfs = storage.VfsFat(sd)
    storage.mount(vfs, '/')
    print(os.listdir('/'))
    return audiocore.WaveFile("/piau.wav")

def read_serial(serial):
    available = serial.in_waiting
    text = ''
    while available:
        raw = serial.read(available)
        text += raw.decode("utf-8")
        available = serial.in_waiting
    return text

serial = usb_cdc.console