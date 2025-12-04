from stepper import Stepper
from machine import Pin, PWM
from time import sleep




Solinoid = Pin(12, Pin.OUT)

buzzerpin = Pin(14, Pin.OUT)
buzzer_pwm = PWM(buzzerpin)

def buzzer(pwm_object, frequency, tone_duration, silence_duration):
    pwm_object.duty(512)
    pwm_object.freq(frequency)
    sleep(tone_duration)
    pwm_object.duty(0)
    sleep(silence_duration)

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
    
buzzer(buzzer_pwm,440,1,0.02)
Solinoid.on()
sleep(1)


 
step(509)
sleep(1)
print(currentStep)
step(-509)
sleep(1)
print(currentStep)
Solinoid.off()
sleep(1)
