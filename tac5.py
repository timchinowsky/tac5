# SPDX-FileCopyrightText: 2024 Tim Chinowsky
# SPDX-License-Identifier: MIT

import array
import audiocore
import board
import math
import os
import sdcardio
import storage
import digitalio

import storage
import time
import usb_cdc

import pcm

status = digitalio.DigitalInOut(board.A1)
status.direction = digitalio.Direction.OUTPUT

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


# wave_width is the width of the datatype used to store samples in memory, and must be 32 or 16 bits
# sample_width is the width of the samples sent to the TAC5, which typically matches the width of the 
# TAC5 serial interface.  Samples sent to the TAC5 are left-justified in memory, so if for instance
# sample_width is 8 but wave_width is 32, samples to be sent to the TAC5 are left-shifted 24 bits
# before being stored in memory.

def new_buffer(length=400, channels=2, sample_width=None, wave_width=32, offset=0, init=None, header=0):
    if wave_width==32:
        wave = array.array('L', [offset] * (length * channels + header))
    elif wave_width==16:
        wave = array.array('H', [offset] * (length * channels + header))
    else:
        raise ValueError("unsupported wave_width")

    if init == 'zero':
        return wave
    if init == 'octave':
        pad_after = 25
        amplitude = 0.7
        if sample_width is None:
            sample_width = wave_width
        for c in range(channels):
            for i in range(length-pad_after-header):
                f = c + 1
                a = 2**(sample_width-1) - 1
                val = int(a * amplitude * math.sin(i/(length - pad_after - header) * 2 * math.pi * f)) + offset
                wave[i*channels+c+header] = int2bits(val, sample_width) << (wave_width-sample_width)
        return wave
    if init == 'sine':
        amplitude = 0.7
        if sample_width is None:
            sample_width = wave_width
        for c in range(channels):
            for i in range(length-header):
                f = 1
                a = 2**(sample_width-1) - 1
                val = int(a * amplitude * math.sin(i/(length - header) * 2 * math.pi * f)) + offset
                wave[i*channels+c+header] = int2bits(val, sample_width) << (wave_width-sample_width)
        return wave
    elif init == 'count':
        if sample_width is None:
            sample_width = wave_width
        for c in range(channels):
            for i in range(length-header):
                val = (i-(length//2)) * channels + c + offset
                wave[i*channels + c + header] = int2bits(val, sample_width) << (wave_width-sample_width)
        return wave
    else:
        return wave

def ring_modulator(length=256, channels=8, sample_width=16, wave_width=16, init='sine', header=2):
    process = new_buffer(length=length, channels=channels, sample_width=sample_width,
                         wave_width=wave_width, init=init, header=header)
    process[0] = 1
    process[1] = 0
    return process

def echo(length=256, channels=8, sample_width=16, wave_width=16, init='zero', header=2):
    process = new_buffer(length=length, channels=channels, sample_width=sample_width,
                         wave_width=wave_width, init=init, header=header)
    process[0] = 2
    process[1] = 0
    return process

def print_tuple(t, format=';'):
    if format is None:
        print(t)
    elif len(format)==1:
        print(str(t).replace(',', format)[1:-1])
    else:
        raise ValueError('Unrecognized format')
    
class TAC5():
    """
    >>> import tac5
    >>> t = tac5.TAC5(channels=2, width=8, sample_rate=12000, address=None)
    >>> t.rec(length=10)
    recording...
    >>> t.play(length=10, test='count')
    playing...
    >>> t.show(t.record_once_buffer)
    >>> t = tac5.TAC5(channels=2, width=24, sample_rate=48000, address=None)   
    >>> t.rec(length=10)
    recording...
    >>> t.play(length=10, test='count')
    playing...
    >>> t.show(t.record_loop_buffer, slice=slice(0,1), show_time=True, loop=True)
    """
    def __init__(self,
                 address='scan',
                 channels=None,
                 i2c=None,
                 clk_pin=board.D5, # sync will be one higher, e.g. D6
                 out_pin=board.D9,
                 in_pin=board.D10,
                 width=32,
                 sample_rate=16000
                ):
        if address is not None:
            if i2c is not None:
                self.i2c = i2c
            else:
                self.i2c = board.I2C()
            if address=='scan':
                self.address = [a for a in self.scan() if a>=0x50 and a<=0x53]
            elif type(address)==int:
                self.address = [address]
            else:
                self.address = address
        else:
            self.address = []
        self.codecs = len(self.address)
        if channels is None:
            self.channels = 2 * self.codecs
        else:
            self.channels = channels
        self.clk_pin = clk_pin
        self.out_pin = out_pin
        self.in_pin = in_pin
        self.slots = {}
        self.width = width
        self.sample_rate=sample_rate
        self.pcm = None
        self.play_once_buffer = None
        self.play_loop_buffer = None
        self.play_loop2_buffer = None
        self.record_once_buffer = None
        self.record_loop_buffer = None
        self.record_loop2_buffer = None

    def deinit(self):
        print('Shutting down...')
        self.pcm.pio.deinit()

    def configure(self, address='all'):
        if self.pcm is not None:
            del self.pcm
        addresses = self.address_list(address)
        for i, a in enumerate(addresses):
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

    def test(self, length=10, slip_time=10, end=False):
        if end:
            self.play(end=True)
            time.sleep(0.1)
            self.rec(end=True)
            time.sleep(0.1)
        self.rec(length=length)
        self.play(length=length, test='count')
        print("\nOnce:")
        self.show(self.record_once_buffer)
        print("\nLoop (1):")
        self.show(self.record_loop_buffer)
        print("\nLoop (2):")
        self.show(self.record_loop_buffer)
        print("\nLoop (3):")
        self.show(self.record_loop_buffer)
        t0 = time.monotonic()
        print(f'\nWatching for slip for {slip_time} seconds...')
        slip_count = 0
        last_rec = self.record_loop_buffer[0]
        while time.monotonic() < t0 + slip_time:
            r = self.record_loop_buffer[0]
            if r != last_rec:
                slip_count += 1
                last_rec = r
        print(f'\nSlip: {slip_count}')
        return slip_count
    
    def play_audiosample(self, sample, channel_select):
        self.pcm.pio.audiosamples.append((sample, channel_select))

    def play(self, filename=None, loop_buffer=None, loop2_buffer=None, once_buffer=None, loop=True, once=True, 
             reset=False, length=None, init='zero', end=False, double_buffer=True, swap=False, repeat=True,
             width=None, process=None):
        if end:
            self.pcm.pio.stop_background_write()
            return
        if reset or self.pcm is None:
            self.configure()

        if width is None:
            width = self.width
        if loop_buffer is None:
            if length is None:
                loop_buffer = new_buffer(channels=self.channels, sample_width=width, init=init)
                if double_buffer:
                    loop2_buffer = new_buffer(channels=self.channels, sample_width=width, init=init, offset=1)
            else:
                loop_buffer = new_buffer(channels=self.channels, sample_width=width, length=length, init=init)
                if double_buffer:
                        loop2_buffer = new_buffer(channels=self.channels, sample_width=width, length=length, init=init, offset=1)
            self.play_loop_buffer = loop_buffer
            if double_buffer:
                self.play_loop2_buffer = loop2_buffer
 
        if once_buffer is None:
            if length is None:
                once_buffer = new_buffer(channels=self.channels, sample_width=width, init=init)
            else:
                once_buffer = new_buffer(channels=self.channels, sample_width=width, length=length, init=init)
            self.play_once_buffer = once_buffer

        print('playing...')
        if once:
            if loop:
                if double_buffer:
                    self.pcm.pio.background_write(once=self.play_once_buffer, loop=self.play_loop_buffer, loop2=self.play_loop2_buffer, swap=swap)
                else:
                    self.pcm.pio.background_write(once=self.play_once_buffer, loop=self.play_loop_buffer, swap=swap)
            else:
                self.pcm.pio.background_write(once=self.play_once_buffer, swap=swap)
        elif loop:
            if double_buffer:
                self.pcm.pio.background_write(loop=self.play_loop_buffer, loop2=self.play_loop2_buffer, swap=swap)
            else:
                self.pcm.pio.background_write(loop=self.play_loop_buffer, swap=swap)

        if filename is not None:
            n = -1
            print('opening', filename, '...')
            with open(filename, 'r') as f:
                while repeat or n == -1:
                    while n != 0:
                        b = self.pcm.pio.last_write
                        if len(b) > 0:
                            status.value = True
                            n = f.readinto(b)
                            status.value = False
                            status.value = True
                            if process is not None:
                                self.pcm.pio.process(b, parameters=process)
                            status.value = False
                    if repeat:
                        print('repeating...')
                        f.seek(0)
                        n = -1

    def rec(self, loop_buffer=None, loop2_buffer=None, once_buffer=None, loop=True, once=True, reset=False, length=None, end=False, double_buffer=False):
        if end:
            self.pcm.pio.stop_background_read()
            return
        if reset or self.pcm is None:
            self.configure()

        if (loop_buffer is None and self.record_loop_buffer is None) or length is not None:
            if self.play_loop_buffer is not None and length is None:
                loop_buffer = array.array('L', [0] * len(self.play_loop_buffer))
                self.record_loop_buffer = loop_buffer
                if double_buffer:
                    loop2_buffer = array.array('L', [0] * len(self.play_loop_buffer))
                    self.record_loop2_buffer = loop2_buffer

            elif length is not None:
                loop_buffer = array.array('L', [0] * length * self.channels)
                self.record_loop_buffer = loop_buffer
                if double_buffer:
                    loop2_buffer = array.array('L', [0] * length * self.channels)
                    self.record_loop2_buffer = loop2_buffer

            elif loop:
                raise ValueError('No loop buffer specified!')
            
        elif loop_buffer is None:
            loop_buffer = self.record_loop_buffer
            if double_buffer:
                loop2_buffer = self.record_loop2_buffer
        else:
            self.record_loop_buffer = loop_buffer
            if double_buffer:
                self.record_loop2_buffer = loop2_buffer

        if (once_buffer is None and self.record_once_buffer is None) or length is not None:
            if self.record_once_buffer is not None and length is None:
                once_buffer = array.array('L', [0] * len(self.play_once_buffer))
                self.record_once_buffer = once_buffer
            elif length is not None:
                once_buffer = array.array('L', [0] * length * self.channels)
                self.record_once_buffer = once_buffer
            elif once:
                raise ValueError('No once buffer specified!')
        elif once_buffer is None:
            once_buffer = self.record_once_buffer
        else:
            self.record_once_buffer = once_buffer

        print('recording...')
        if once:
            if loop:
                if double_buffer:
                    self.pcm.pio.background_read(once=self.record_once_buffer, loop=self.record_loop_buffer, loop2=self.record_loop2_buffer)
                else:
                    self.pcm.pio.background_read(once=self.record_once_buffer, loop=self.record_loop_buffer)
            else:
                self.pcm.pio.background_read(once=self.record_once_buffer)
        elif loop:
            if double_buffer:
                self.pcm.pio.background_read(loop=self.record_loop_buffer, loop2=self.record_loop2_buffer)
            else:
                self.pcm.pio.background_read(loop=self.record_loop_buffer)

    def tape(self, filename):
        with open(filename, 'w') as f:
            while True:
                f.write(self.pcm.pio.last_read)

    def show(self, buffer, slice=slice(None), format=';', shift=True, show_time=False, loop=False, delay=0):
        once = True
        header = ('sample')
        if show_time:
            header = ('sample', 'time')
        else:
            header = ('sample',)
        header += tuple([f'ch{j}' for j in range(self.channels)])
        if shift:
            rshift = 0
        else:
            rshift = 32-self.width
        print_tuple(header, format)
        t0 = int(time.monotonic()*1000)
        while once or loop:
            for i in range(len(buffer)//self.channels)[slice]:
                if show_time:
                    data = (i,int(time.monotonic()*1000)-t0)
                else:
                    data = (i,)
                data += tuple([bits2int(buffer[j+i*self.channels] >> rshift, self.width) for j in range(self.channels)])
                if delay > 0:
                    time.sleep(delay)
                print_tuple(data, format)
            once = False

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

# sd = sdcardio.SDCard(board.SPI(), board.D25)
# vfs = storage.VfsFat(sd)
# storage.mount(vfs, '/')

def wav():
    print(os.listdir('/'))
    return audiocore.WaveFile("/piau.wav")

def write_test(segment=1000, n=1000):
    print(os.listdir('/'))
    f = open('/foo.txt', 'w')
    l = [i%255 for i in range(segment)]
    b = bytes(l)
    t0 = time.monotonic()
    for i in range(n):
        f.write(b)
    t1 = time.monotonic()    
    f.close()
    print(os.stat('/foo.txt'))
    print(f'wrote {segment*n} bytes in {t1-t0} seconds ({segment*n/(t1-t0)} bytes/s).')

def read_serial(serial):
    available = serial.in_waiting
    text = ''
    while available:
        raw = serial.read(available)
        text += raw.decode("utf-8")
        available = serial.in_waiting
    return text

serial = usb_cdc.console
