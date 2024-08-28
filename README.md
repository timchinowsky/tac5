# tac5

This code provides CircuitPython support for TI's TAC5xxx audio codecs on rp2040 processors.

* `pcm.py` uses the rp2040 PIO to implement a PCM interface to the TAC5 which supports arbitrary numbers of channels, word sizes, and sample rates.

* `tac5.py` implements a TAC5 class which knows how to initialize the TAC5xxx over I2C, write to its DACs, and read from its ADCs.

* If multiple TAC5xxx parts with different I2C addresses are present, they are assumed to be wired in parallel for multichannel operation and configured appropriately.  In this mode, each chip is assigned a time slot in the DOUT signal, and makes it Hi-Z at other times.  

* In the example shown here, 4 TAC5212's are connected with BCLK, FSYNC, DOUT, DIN, SCL, and SDA in parallel.  On each chip, the differential DAC outputs are connected to the differential ADC inputs to provide test signals for the ADCs, i.e. OUT1P -> IN1P, OUT1M -> IN1M, OUT2P -> IN2P, OUT2M -> IN2M.

* Chips are mounted on breakout boards.  KiCad files for these boards are [here](pcb).  Schematic and layout look like this:

![images/]

## Example

```python
Adafruit CircuitPython 9.1.0-beta.3-76-g2f62612186-dirty on 2024-07-18; Adafruit Feather RP2040 with rp2040
>>> 
>>> import tac5
>>> t = tac5.TAC5()
>>> t.play()
playing...
>>> t.record()
sample,record0,record1,record2,record3,record4,record5,record6,record7,
0,-1,-1,-1,-1,-21642,13829,-311,-14878,
1,22355,-20880,-14861,22749,-19762,8760,6829,-20253,
2,21608,-13819,-15930,23037,-17183,3144,13306,-22783,
3,18190,-3347,-16936,22962,-13994,-2666,18482,-22111,
4,10858,7951,-17876,22524,-10311,-8313,21849,-18332,
5,602,17291,-18745,21730,-6263,-13440,23074,-11979,
6,-8849,22352,-19541,20593,-1995,-17725,22040,-3945,
7,-17070,21881,-20257,19131,2341,-20896,18845,4634,
8,-21748,15993,-20896,17368,6597,-22754,13806,12577,
9,-22490,6149,-21452,15331,10620,-23182,7417,18743,
...(115 more lines)
```

