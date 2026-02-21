def abort(chan):
    chan.write(f"{chan.channel}.abort()")

def meas_trig_params(chan):
    #setup buffer
    chan.write(f"{chan.channel}.measure.autozero = 1")
    chan.write(f"{chan.channel}.trigger.measure.i({chan.channel}.nvbuffer1)")
    chan.write(f"{chan.channel}.nvbuffer1.appendmode = 1")

    #clear any residual values
    chan.write(f"{chan.channel}.nvbuffer1.clear()")
    chan.write(f"{chan.channel}.nvbuffer1.collectsourcevalues = 1")

    #set measure trig to automatic (after source)
    chan.write(f"{chan.channel}.measure.count = 1")
    chan.write(f"{chan.channel}.trigger.measure.stimulus = 0")

    #enable
    chan.write(f"{chan.channel}.trigger.measure.action = {chan.channel}.ENABLE")


def source_trig_params(chan):
    #tie source to bus trigger
    chan.write(f"{chan.channel}.trigger.source.stimulus = trigger.EVENT_ID")

    #end of sweep phase action
    chan.write(f"{chan.channel}.trigger.endsweep.action = smua.SOURCE_HOLD")

    #enable
    chan.write(f"{chan.channel}.trigger.source.action = {chan.channel}.ENABLE")


def trigger(keithleys, channels):
    for ch in channels:
        ch.write(f"{ch.channel}.nvbuffer1.clear()")
        ch.write(f"{ch.channel}.trigger.initiate()")

    for k in keithleys:
        k.write("*TRG")


def recall_buffer(ch):
    j = ch.ask(f"{ch.channel}.nvbuffer1.readings[1]") 
    v = ch.ask(f"{ch.channel}.nvbuffer1.sourcevalues[1]")
    return v, j


def set_v(ch,volt):
    volt = str(volt)
    ch.write(f"{ch.channel}.trigger.source.linearv({volt}, {volt}, 1)")


    

