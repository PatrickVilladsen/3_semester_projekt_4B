from stepper import Stepper
from machine import Pin
from time import sleep


In1 = Pin(16, Pin.OUT)
In2 = Pin(17, Pin.OUT)
In3 = Pin(5, Pin.OUT)
In4 = Pin(18, Pin.OUT)
delay = 1
mode = 0

currentStep = 0


s1 = Stepper(In1, In2, In3, In4, delay, mode)
def step(count):
    global currentStep
    currentStep = currentStep + count
    s1.step(count)
    
step(509)
sleep(1)
print(currentStep)
step(-509)
sleep(1)
print(currentStep)

