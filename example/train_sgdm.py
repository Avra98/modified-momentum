import argparse
import torch

from model.wide_res_net import WideResNet
from model.smooth_cross_entropy import smooth_crossentropy
from data.cifar import Cifar10, Cifar100
from utility.log import Log
from utility.initialize import initialize
from utility.step_lr import StepLR
from utility.bypass_bn import enable_running_stats, disable_running_stats

import sys; sys.path.append("..")
from sgdm import SGDM
from sgd import SGD

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--adaptive", '-apt', action='store_false', help="True if you want to use the Adaptive SAM: default True")
    parser.add_argument("--batch_size", default=100, type=int, help="Batch size used in the training and validation loop.")
    parser.add_argument("--depth", default=10, type=int, help="Number of layers.")
    parser.add_argument("--dropout", default=0.0, type=float, help="Dropout rate.")
    parser.add_argument("--epochs", default=2, type=int, help="Total number of epochs.")
    parser.add_argument("--label_smoothing", default=0.1, type=float, help="Use 0.0 for no label smoothing.")
    parser.add_argument("--learning_rate", '-lr', default=1e-1, type=float, help="Base learning rate at the start of the training.")
    parser.add_argument("--momentum", '-beta', default=0.8, type=float, help="SGD Momentum.")
    parser.add_argument("--scheduler", "-shd", action='store_true', help="if using scheduler")
    parser.add_argument("--dataset", default="cifar10", type=str, help="dataset name")

    parser.add_argument("--threads", default=4, type=int, help="Number of CPU threads for dataloaders.")
    #parser.add_argument("--rho", default=2.0, type=int, help="Rho parameter for SAM.")
    parser.add_argument("--weight_decay", default=0.0000, type=float, help="L2 weight decay.")
    parser.add_argument("--width_factor", default=4, type=int, help="How many times wider compared to normal ResNet.")
    #parser.add_argument("dampening", default=0.0, type=float,help="dampening")
    #parser.add_argument("nesterov", default=False, type=bool,help="nesterov")
    args = parser.parse_args()

    initialize(args, seed=42)
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")

    in_channels = 3
    labels = 100
    if args.dataset.lower() == 'cifar10':
        dataset = Cifar10(args.batch_size, args.threads)
        labels = 10
    if args.dataset.lower() == 'cifar100':
        dataset = Cifar100(args.batch_size, args.threads)

    log = Log(log_each=10, file_name= args.dataset+'lr'+str(int(1e3*args.learning_rate))
                                          +'beta'+str(int(10*args.momentum))
                                          +'ls'+str(int(10*args.label_smoothing))
                                          +'shd'+str(args.scheduler)
                                          +'width'+str(args.width_factor)
                                          +'depth'+str(args.depth)
                                          +'adaptive'+str(args.adaptive))

    ## Use in_channels=1 if MNIST or 3 if CIFAR-10. 
    model = WideResNet(args.depth, args.width_factor, args.dropout, in_channels=in_channels, labels=labels).to(device)

    #base_optimizer = torch.optim.SGD
    if args.adaptive:
        optimizer = SGDM(model.parameters(), lr=args.learning_rate, momentum=args.momentum, dampening=0, weight_decay=args.weight_decay,nesterov = False)
    else:
        optimizer = SGD(model.parameters(),lr=args.learning_rate, momentum=args.momentum, weight_decay=args.weight_decay)
    scheduler = StepLR(optimizer, args.learning_rate, args.epochs)
    
    for epoch in range(args.epochs):
        model.train()
        log.train(len_dataset=len(dataset.train))
        iter = 0
        loss_count = 0
        print(len(dataset.train))
        for batch in dataset.train:
            inputs, targets = (b.to(device) for b in batch)
           
            # get the first term \grad E_{n}(x(tn)+\delta x1) and store it in self.state[p]["pres_grad"] (look at sgdm.py))
            enable_running_stats(model)
            predictions = model(inputs)
            loss = smooth_crossentropy(predictions, targets, smoothing=args.label_smoothing)
            loss.mean().backward()
            
            # SGD
            if args.adaptive == False:
                optimizer.step()
                with torch.no_grad():
                    predictions = model(inputs)
                    correct = torch.argmax(predictions.data, 1) == targets
                    log(model, loss.cpu(), correct.cpu(), scheduler.lr())
                    if args.scheduler:
                        scheduler(epoch)
                continue

            optimizer.first_step(zero_grad=True)

            # SGDM
            disable_running_stats(model)
            loss = smooth_crossentropy(model(inputs),targets, smoothing=args.label_smoothing)
            loss.mean().backward()

            optimizer.second_step(zero_grad=False,mode ="first")

            ### make the original update
            optimizer.step(iter)
            optimizer.zero_grad()            
            
            ### store the second term to be used for next iteration. Store it in self.state[p]["pre_grad"] (look at sgdm.py))
            enable_running_stats(model)
            loss = smooth_crossentropy(model(inputs), targets, smoothing=args.label_smoothing)
            loss.mean().backward()
            optimizer.first_step(zero_grad=True)
 
            disable_running_stats(model)
            loss = smooth_crossentropy(model(inputs), targets, smoothing=args.label_smoothing)
            loss.mean().backward()
            optimizer.second_step(zero_grad=True,mode ="second")
            iter=iter+1

            with torch.no_grad():
                predictions = model(inputs)
                correct = torch.argmax(predictions.data, 1) == targets
                log(model, loss.cpu(), correct.cpu(), scheduler.lr())
                if args.scheduler:
                    scheduler(epoch)

        optimizer.zero_grad()
        
        model.eval()
        log.eval(len_dataset=len(dataset.test))
    
        with torch.no_grad():
            print(len(dataset.test))
            for batch in dataset.test:
                inputs, targets = (b.to(device) for b in batch)
                predictions2 = model(inputs)
                loss = smooth_crossentropy(predictions2, targets)
                correct = torch.argmax(predictions2, 1) == targets
                log(model, loss.cpu(), correct.cpu())
           

    log.flush()
