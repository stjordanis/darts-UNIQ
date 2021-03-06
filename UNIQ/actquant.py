import torch.nn as nn
from UNIQ.quantize import act_quantize, act_noise, check_quantization
import torch.nn.functional as F


class ActQuant(nn.Module):

    def __init__(self, quatize_during_training=False, noise_during_training=False, quant=False, noise=False,
                 bitwidth=32):
        super(ActQuant, self).__init__()
        self.quant = quant
        self.noise = noise
        self.bitwidth = bitwidth
        self.quatize_during_training = quatize_during_training
        self.noise_during_training = noise_during_training

    def update_stage(self, quatize_during_training=False, noise_during_training=False):
        self.quatize_during_training = quatize_during_training
        self.noise_during_training = noise_during_training

    def forward(self, input):
        if self.quant and (not self.training or (self.training and self.quatize_during_training)):
            assert (isinstance(self.bitwidth, int))
            x = act_quantize.apply(input, self.bitwidth)
        elif self.noise and self.training and self.noise_during_training:
            assert (False)
            x = act_noise.apply(input, bitwidth=self.bitwidth, training=self.training)
        else:
            x = F.relu(input)

        # print('Activation is quantized to {} values'.format(check_quantization(x)))
        return x
