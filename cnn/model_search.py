from torch import cat, randn
from torch.nn import Module, ModuleList, Sequential, BatchNorm2d, Conv2d, AdaptiveAvgPool2d, Linear
import torch.nn.functional as F
from torch.autograd import Variable
# from cnn.genotypes import PRIMITIVES, Genotype
from cnn.operations import OPS, FactorizedReduce, ReLUConvBN
from UNIQ.uniq import UNIQNet
from UNIQ.actquant import ActQuant


class MixedOp(Module):
    def __init__(self, C, stride):
        super(MixedOp, self).__init__()
        self._ops = ModuleList()
        for key, opFunc in OPS.items():
            op = opFunc(C, stride, False)
            if 'pool' in key:
                op = Sequential(op, BatchNorm2d(C, affine=False))
            self._ops.append(op)

    def forward(self, x, weights):
        return sum(w * op(x) for w, op in zip(weights, self._ops))

    # def __init__(self, C, stride):
    #     super(MixedOp, self).__init__()
    #     self._ops = ModuleList()
    #     for primitive in PRIMITIVES:
    #         op = OPS[primitive](C, stride, False)
    #         if 'pool' in primitive:
    #             op = Sequential(op, BatchNorm2d(C, affine=False))
    #         self._ops.append(op)


class Cell(Module):
    # steps - number of nodes in the cell
    def __init__(self, steps, multiplier, C_prev_prev, C_prev, C, reduction, reduction_prev):
        super(Cell, self).__init__()
        self.reduction = reduction

        if reduction_prev:
            self.preprocess0 = FactorizedReduce(C_prev_prev, C, affine=False)
        else:
            self.preprocess0 = ReLUConvBN(C_prev_prev, C, 1, 1, 0, affine=False)
        self.preprocess1 = ReLUConvBN(C_prev, C, 1, 1, 0, affine=False)
        self._steps = steps
        self._multiplier = multiplier

        self._ops = ModuleList()
        self._bns = ModuleList()
        for i in range(self._steps):
            for j in range(2 + i):
                # TODO: why do they use this j index ???
                stride = 2 if reduction and j < 2 else 1
                op = MixedOp(C, stride)
                self._ops.append(op)

    def forward(self, s0, s1, weights):
        s0 = self.preprocess0(s0)
        s1 = self.preprocess1(s1)

        states = [s0, s1]
        offset = 0
        for i in range(self._steps):
            s = sum(self._ops[offset + j](h, weights[offset + j]) for j, h in enumerate(states))
            offset += len(states)
            states.append(s)

        return cat(states[-self._multiplier:], dim=1)


class Network(Module):

    def __init__(self, C, num_classes, layers, criterion, steps=4, multiplier=4, stem_multiplier=3):
        super(Network, self).__init__()
        self._C = C
        self._num_classes = num_classes
        self._layers = layers  # each layer is a Cell object
        self._criterion = criterion
        self._steps = steps
        self._multiplier = multiplier
        # init number of layers we have completed its quantization
        self.nLayersQuantCompleted = 0

        C_curr = stem_multiplier * C
        self.stem = Sequential(
            Conv2d(3, C_curr, 3, padding=1, bias=False),
            BatchNorm2d(C_curr)
        )

        C_prev_prev, C_prev, C_curr = C_curr, C_curr, C
        self.cells = ModuleList()
        reduction_prev = False
        for i in range(layers):
            if i in [layers // 3, 2 * layers // 3]:
                C_curr *= 2
                reduction = True
            else:
                reduction = False

            cell = Cell(steps, multiplier, C_prev_prev, C_prev, C_curr, reduction, reduction_prev)
            reduction_prev = reduction
            self.cells += [cell]
            C_prev_prev, C_prev = C_prev, multiplier * C_curr

        self.global_pooling = AdaptiveAvgPool2d(1)
        self.classifier = Linear(C_prev, num_classes)

        self._initialize_alphas()

        # set learnable parameters
        self.learnable_params = [param for param in self.parameters() if param.requires_grad]
        # update model parameters() function
        self.parameters = self.getLearnableParams

    def forward(self, input):
        s0 = s1 = self.stem(input)
        for i, cell in enumerate(self.cells):
            if cell.reduction:
                weights = F.softmax(self.alphas_reduce, dim=-1)
            else:
                weights = F.softmax(self.alphas_normal, dim=-1)
            s0, s1 = s1, cell(s0, s1, weights)
        out = self.global_pooling(s1)
        logits = self.classifier(out.view(out.size(0), -1))
        return logits

    def _loss(self, input, target):
        logits = self(input)
        return self._criterion(logits, target)

    def _initialize_alphas(self):
        k = sum(1 for i in range(self._steps) for n in range(2 + i))
        # num_ops = len(PRIMITIVES)
        num_ops = len(OPS)

        self.alphas_normal = Variable(1e-3 * randn(k, num_ops).cuda(), requires_grad=True)
        self.alphas_reduce = Variable(1e-3 * randn(k, num_ops).cuda(), requires_grad=True)
        self._arch_parameters = [
            self.alphas_normal,
            self.alphas_reduce,
        ]

    def arch_parameters(self):
        return self._arch_parameters

    def getLearnableParams(self):
        return self.learnable_params

    def switch_stage(self, logger=None):
        switchStageLayerExists = False
        cell = self.cells[self.nLayersQuantCompleted]
        for mixedOp in cell._ops:
            if not isinstance(mixedOp, MixedOp):
                continue

            for op in mixedOp._ops:
                if isinstance(op, UNIQNet):
                    for m in op.modules():
                        if isinstance(m, Conv2d):
                            switchStageLayerExists = True
                            for param in m.parameters():
                                param.requires_grad = False
                        elif isinstance(m, ActQuant):
                            switchStageLayerExists = True
                            m.quatize_during_training = True
                            m.noise_during_training = False

        # update learnable parameters
        self.learnable_params = [param for param in self.parameters() if param.requires_grad]

        # we have completed quantization of one more layer
        self.nLayersQuantCompleted += 1

        if logger and switchStageLayerExists:
            logger.info('Switching stage, nLayersQuantCompleted:[{}]'.format(self.nLayersQuantCompleted))

# def genotype(self):
#     def _parse(weights):
#         gene = []
#         n = 2
#         start = 0
#         for i in range(self._steps):
#             end = start + n
#             W = weights[start:end].copy()
#             edges = sorted(range(i + 2),
#                            key=lambda x: -max(W[x][k] for k in range(len(W[x])) if k != PRIMITIVES.index('none')))[:2]
#             for j in edges:
#                 k_best = None
#                 for k in range(len(W[j])):
#                     if k != PRIMITIVES.index('none'):
#                         if k_best is None or W[j][k] > W[j][k_best]:
#                             k_best = k
#                 gene.append((PRIMITIVES[k_best], j))
#             start = end
#             n += 1
#         return gene
#
#     gene_normal = _parse(F.softmax(self.alphas_normal, dim=-1).data.cpu().numpy())
#     gene_reduce = _parse(F.softmax(self.alphas_reduce, dim=-1).data.cpu().numpy())
#
#     concat = range(2 + self._steps - self._multiplier, self._steps + 2)
#     genotype = Genotype(
#         normal=gene_normal, normal_concat=concat,
#         reduce=gene_reduce, reduce_concat=concat
#     )
#     return genotype
